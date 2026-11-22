"""Warm pool of language servers keyed by (server, root)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import LspClient, LspError, norm_path
from .registry import IS_WINDOWS, Registry, ServerSpec

log = logging.getLogger("ripplelens.lsp")


def jdtls_failure_hint(cmd: list[str], since: float) -> str | None:
    """jdtls writes startup failures to <install>/configuration/<n>.log; name the Java it needs."""
    import re

    launcher = next((Path(c) for c in cmd if Path(c).name.lower().startswith("jdtls")), None)
    if launcher is None:
        return None
    logs = sorted((launcher.parent.parent / "configuration").glob("*.log"), key=lambda p: p.stat().st_mtime)
    wall_since = time.time() - (time.monotonic() - since) - 5
    if not logs or logs[-1].stat().st_mtime < wall_since:
        return None
    text = logs[-1].read_text(encoding="utf-8", errors="replace")[:200_000]
    need = [int(v) for v in re.findall(r"osgi\.ee=JavaSE\)\(version=(\d+)\)", text)]
    have = re.search(r"java\.version=(\S+)", text)
    if need:
        return (
            f"this jdtls build needs Java {max(need)}+ but runs on Java {have.group(1) if have else '?'}; "
            f"install JDK {max(need)} and set JAVA_HOME (log: {logs[-1]})"
        )
    return f"jdtls failed during startup; see {logs[-1]}"


class ServerUnavailable(Exception):
    def __init__(self, spec: ServerSpec, reason: str):
        super().__init__(f"{spec.name}: {reason}")
        self.spec = spec
        self.reason = reason


@dataclass
class ServerHandle:
    spec: ServerSpec
    root: Path
    client: LspClient
    started_at: float
    init_seconds: float = 0.0
    ready: bool = False
    ready_seconds: float | None = None
    ready_reason: str = ""
    warmed_files: set[str] = field(default_factory=set)
    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def version(self) -> str | None:
        """Server version; gopls reports its whole build info as JSON."""
        info = self.client.server_info or {}
        v = info.get("version")
        if isinstance(v, str) and v.startswith("{"):
            try:
                import json

                return json.loads(v).get("Version") or v[:40]
            except ValueError:
                return v[:40]
        return v

    def language_id(self, path: str) -> str:
        return self.spec.language_id(path) or "plaintext"

    def ensure_open(self, path: str, text: str | None = None) -> None:
        if text is not None or not self.client.is_open(path):
            self.client.open_document(path, self.language_id(path), text)

    def sync_from_disk(self, changed: list[tuple[str, int]]) -> None:
        """Make a warm server see the current files: refresh open documents and report disk changes."""
        with self.lock:
            for key in self.client.open_paths():
                try:
                    text = Path(key).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    self.client.close_document(key)
                    continue
                self.client.open_document(key, self.language_id(key), text)
            try:
                self.client.notify_files_changed(changed)
            except LspError:
                pass

    def _has_index_inputs(self) -> bool:
        return any((self.root / d / "compile_commands.json").exists() for d in ("", "build", "out", "cmake-build-debug"))

    def warm_up(self, sample_files: list[str]) -> None:
        """Open sample files and wait for indexing to settle (readiness)."""
        new = [f for f in sample_files if norm_path(f) not in self.warmed_files]
        if self.ready and not new:
            return
        t0 = time.monotonic()
        for f in new[:3]:
            try:
                self.ensure_open(f)
            except OSError:
                continue
            self.warmed_files.add(norm_path(f))
        remaining = max(5.0, self.spec.startup_timeout - (time.monotonic() - self.started_at))
        if self.spec.index_progress and new and self._has_index_inputs():
            # clangd starts background indexing only after the first file loads the
            # compilation database; a quiet period before that is not readiness
            self.client.wait_progress_title(self.spec.index_progress, timeout=min(20.0, remaining))
        ok = self.client.wait_quiet(
            timeout=remaining,
            quiet=2.0 if self.spec.name in ("clangd", "jdtls") else 1.0,
            require_service_ready=self.spec.readiness == "service_ready",
        )
        if ok and new:
            # probe: a cheap request that forces the server to finish loading the file
            try:
                self.client.document_symbols(new[0], timeout=min(120.0, remaining))
            except LspError as e:
                ok = False
                self.ready_reason = f"probe failed: {e}"
            ok = ok and self.client.wait_quiet(timeout=remaining, quiet=1.0)
        self.ready = ok
        if ok:
            self.ready_seconds = (self.ready_seconds or 0.0) + time.monotonic() - t0
            self.ready_reason = ""
        elif not self.ready_reason:
            seen, active = self.client.progress_snapshot()
            self.ready_reason = (
                f"not ready after {self.spec.startup_timeout:.0f}s (active progress: {active or 'none'};"
                f" service_ready={self.client.service_ready})"
            )


class ServerPool:
    def __init__(self, registry: Registry | None = None):
        self.registry = registry or Registry.load()
        self._servers: dict[tuple[str, str], ServerHandle] = {}
        self._failed: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def get(self, spec: ServerSpec, root: str | Path) -> ServerHandle:
        root = Path(root).resolve()
        key = (spec.name, norm_path(root))
        with self._lock:
            handle = self._servers.get(key)
            if handle and handle.client.alive:
                return handle
            if key in self._failed:
                raise ServerUnavailable(spec, self._failed[key])
        cmd = spec.build_command(root)
        if not cmd:
            reason = f"executable '{spec.command}' not found; install: {spec.install.get('windows' if IS_WINDOWS else 'linux', '')}"
            with self._lock:
                self._failed[key] = reason
            raise ServerUnavailable(spec, reason)
        client = LspClient(
            cmd,
            root,
            env=spec.env,
            initialization_options=spec.init_options_for(root),
            settings=spec.settings,
            name=spec.name,
        )
        t0 = time.monotonic()
        try:
            client.start()
            client.initialize(timeout=min(spec.startup_timeout, 300))
        except (OSError, LspError) as e:
            reason = f"failed to start: {e}; stderr: {client.stderr_excerpt(5)}"
            if spec.name == "jdtls":
                reason = jdtls_failure_hint(cmd, t0) or reason
            with self._lock:
                self._failed[key] = reason
            try:
                client.shutdown(timeout=2)
            except Exception:
                pass
            raise ServerUnavailable(spec, reason) from e
        handle = ServerHandle(spec=spec, root=root, client=client, started_at=t0, init_seconds=time.monotonic() - t0)
        with self._lock:
            self._servers[key] = handle
        return handle

    def handles(self) -> list[ServerHandle]:
        with self._lock:
            return list(self._servers.values())

    def status(self) -> list[dict[str, Any]]:
        out = []
        for h in self.handles():
            out.append(
                {
                    "server": h.name,
                    "root": str(h.root),
                    "alive": h.client.alive,
                    "ready": h.ready,
                    "init_seconds": round(h.init_seconds, 2),
                    "ready_seconds": round(h.ready_seconds, 2) if h.ready_seconds is not None else None,
                    "server_info": h.client.server_info,
                }
            )
        return out

    def release_root(self, root: str | Path) -> None:
        key_root = norm_path(root)
        with self._lock:
            victims = [k for k in self._servers if k[1] == key_root]
            handles = [self._servers.pop(k) for k in victims]
        for h in handles:
            try:
                h.client.shutdown(timeout=3)
            except Exception:
                pass

    def shutdown(self) -> None:
        with self._lock:
            handles = list(self._servers.values())
            self._servers.clear()
        for h in handles:
            try:
                h.client.shutdown(timeout=3)
            except Exception:
                pass
