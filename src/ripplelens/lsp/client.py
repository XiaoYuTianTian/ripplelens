"""Minimal, dependency-free LSP client over stdio (JSON-RPC 2.0).

Only what ripplelens needs: request/notify, server->client requests,
$/progress tracking for readiness, publishDiagnostics capture.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

log = logging.getLogger("ripplelens.lsp")


class LspError(Exception):
    def __init__(self, message: str, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class LspTimeout(LspError):
    pass


class LspServerDied(LspError):
    pass


CLIENT_CAPABILITIES: dict[str, Any] = {
    "general": {"positionEncodings": ["utf-16"]},
    "window": {"workDoneProgress": True, "showMessage": {}},
    "workspace": {
        "configuration": True,
        "workspaceFolders": True,
        "didChangeWatchedFiles": {"dynamicRegistration": True},
        "symbol": {"dynamicRegistration": False},
    },
    "textDocument": {
        "synchronization": {"didSave": True, "dynamicRegistration": False},
        "documentSymbol": {
            "hierarchicalDocumentSymbolSupport": True,
            "symbolKind": {"valueSet": list(range(1, 27))},
        },
        "hover": {"contentFormat": ["markdown", "plaintext"]},
        "references": {},
        "implementation": {"linkSupport": False},
        "definition": {"linkSupport": False},
        "callHierarchy": {"dynamicRegistration": False},
        "typeHierarchy": {"dynamicRegistration": False},
        "publishDiagnostics": {"relatedInformation": False, "versionSupport": True},
        "diagnostic": {"dynamicRegistration": False, "relatedDocumentSupport": False},
    },
}


def path_to_uri(path: str | os.PathLike[str]) -> str:
    return Path(path).resolve().as_uri()


def uri_to_path(uri: str) -> str:
    """Normalise file URIs from any server (file:///c%3A/x, file:///C:/x, ...)."""
    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname

    parsed = urlparse(uri)
    if parsed.scheme not in ("file", ""):
        return uri
    p = url2pathname(unquote(parsed.path))
    if sys.platform == "win32" and len(p) >= 3 and p[0] in "\\/" and p[2] == ":":
        p = p[1:]
    return os.path.normcase(os.path.normpath(p))


def norm_path(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


_LISTINGS: dict[str, dict[str, str]] = {}


def _listing(d: str, refresh: bool = False) -> dict[str, str]:
    if refresh or d not in _LISTINGS:
        try:
            _LISTINGS[d] = {e.name.lower(): e.name for e in os.scandir(d)}
        except OSError:
            _LISTINGS[d] = {}
    return _LISTINGS[d]


def true_case(path: str | os.PathLike[str]) -> str:
    """The on-disk spelling of `path` on case-insensitive file systems.

    Servers such as tsserver report lower-cased URIs on Windows and macOS, and
    `uri_to_path` lower-cases on Windows; report paths must keep the real case.
    """
    p = os.path.abspath(path)
    if sys.platform not in ("win32", "darwin"):
        return p
    drive, rest = os.path.splitdrive(p)
    cur = (drive.upper() if len(drive) == 2 else drive) + os.sep
    for part in [x for x in rest.split(os.sep) if x]:
        key = part.lower()
        real = _listing(cur).get(key) or _listing(cur, refresh=True).get(key) or part
        cur = os.path.join(cur, real)
    return cur


class LspClient:
    def __init__(
        self,
        command: list[str],
        root: str | os.PathLike[str],
        *,
        env: dict[str, str] | None = None,
        initialization_options: Any = None,
        settings: dict[str, Any] | None = None,
        name: str = "lsp",
    ) -> None:
        self.name = name
        self.command = command
        self.root = Path(root).resolve()
        self.initialization_options = initialization_options
        self.settings = settings or {}
        self._env = env
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 0
        self._pending: dict[int, Future[Any]] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._alive = False
        self.stderr_tail: deque[str] = deque(maxlen=200)
        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        # readiness bookkeeping
        self._progress_active: dict[Any, str] = {}
        self._progress_seen = 0
        self.progress_titles: set[str] = set()
        self._last_activity = time.monotonic()
        self._cond = threading.Condition()
        self.status_messages: deque[str] = deque(maxlen=50)
        self.service_ready = False  # jdtls language/status ServiceReady
        # diagnostics: normalised path -> (version, list)
        self.diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._diag_events: dict[str, float] = {}
        self._open_versions: dict[str, int] = {}
        self.notification_handlers: dict[str, Callable[[Any], None]] = {}

    # ------------------------------------------------------------------ process
    def start(self) -> None:
        env = os.environ.copy()
        if self._env:
            env.update(self._env)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.root),
            env=env,
            creationflags=creationflags,
        )
        self._alive = True
        threading.Thread(target=self._read_loop, name=f"{self.name}-out", daemon=True).start()
        threading.Thread(target=self._stderr_loop, name=f"{self.name}-err", daemon=True).start()

    def initialize(self, timeout: float = 120.0) -> dict[str, Any]:
        params = {
            "processId": os.getpid(),
            "clientInfo": {"name": "ripplelens"},
            "rootUri": path_to_uri(self.root),
            "rootPath": str(self.root),
            "workspaceFolders": [{"uri": path_to_uri(self.root), "name": self.root.name}],
            "capabilities": CLIENT_CAPABILITIES,
            "initializationOptions": self.initialization_options,
        }
        result = self.request("initialize", params, timeout=timeout)
        self.capabilities = result.get("capabilities", {}) or {}
        self.server_info = result.get("serverInfo", {}) or {}
        self.notify("initialized", {})
        if self.settings:
            self.notify("workspace/didChangeConfiguration", {"settings": self.settings})
        return result

    def shutdown(self, timeout: float = 5.0) -> None:
        if not self._proc:
            return
        try:
            if self._alive:
                self.request("shutdown", None, timeout=timeout)
                self.notify("exit", None)
        except LspError:
            pass
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._alive = False

    @property
    def alive(self) -> bool:
        return self._alive and self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------ io
    def _send(self, payload: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise LspServerDied(f"{self.name}: not started")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            try:
                self._proc.stdin.write(header + body)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self._alive = False
                raise LspServerDied(f"{self.name}: write failed: {e}") from e

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        out = self._proc.stdout
        try:
            while True:
                length = None
                while True:
                    line = out.readline()
                    if not line:
                        raise EOFError
                    line = line.strip()
                    if not line:
                        break
                    k, _, v = line.decode("ascii", "replace").partition(":")
                    if k.lower() == "content-length":
                        length = int(v.strip())
                if length is None:
                    continue
                body = out.read(length)
                try:
                    msg = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    log.warning("%s: bad json from server", self.name)
                    continue
                self._dispatch(msg)
        except (EOFError, OSError, ValueError):
            pass
        finally:
            self._alive = False
            with self._lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for fut in pending:
                if not fut.done():
                    fut.set_exception(LspServerDied(f"{self.name}: server exited; stderr: {self.stderr_excerpt()}"))
            with self._cond:
                self._cond.notify_all()

    def _stderr_loop(self) -> None:
        assert self._proc and self._proc.stderr
        for raw in iter(self._proc.stderr.readline, b""):
            self.stderr_tail.append(raw.decode("utf-8", "replace").rstrip())

    def stderr_excerpt(self, n: int = 15) -> str:
        return "\n".join(list(self.stderr_tail)[-n:])

    def _touch(self) -> None:
        with self._cond:
            self._last_activity = time.monotonic()
            self._cond.notify_all()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg and "method" not in msg:
            with self._lock:
                fut = self._pending.pop(msg["id"], None)
            if fut is None or fut.done():
                return
            if "error" in msg and msg["error"] is not None:
                err = msg["error"]
                fut.set_exception(LspError(err.get("message", "error"), err.get("code"), err.get("data")))
            else:
                fut.set_result(msg.get("result"))
            return
        method = msg.get("method", "")
        params = msg.get("params")
        if "id" in msg:
            self._handle_server_request(msg["id"], method, params)
            return
        self._handle_notification(method, params)

    def _handle_server_request(self, rid: Any, method: str, params: Any) -> None:
        result: Any = None
        if method == "workspace/configuration":
            items = (params or {}).get("items", [])
            result = [self._config_for(item.get("section")) for item in items]
        elif method == "workspace/workspaceFolders":
            result = [{"uri": path_to_uri(self.root), "name": self.root.name}]
        elif method == "window/workDoneProgress/create":
            self._touch()
        elif method == "workspace/applyEdit":
            result = {"applied": False}
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    def _config_for(self, section: str | None) -> Any:
        if not section:
            return self.settings or None
        node: Any = self.settings
        for part in section.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return None
        return node

    def _handle_notification(self, method: str, params: Any) -> None:
        if method == "$/progress":
            token = params.get("token")
            value = params.get("value") or {}
            kind = value.get("kind")
            with self._cond:
                if kind == "begin":
                    self._progress_active[token] = value.get("title", "")
                    self._progress_seen += 1
                    self.progress_titles.add(str(value.get("title", "")).lower())
                elif kind == "end":
                    self._progress_active.pop(token, None)
                self._last_activity = time.monotonic()
                self._cond.notify_all()
        elif method == "textDocument/publishDiagnostics":
            path = uri_to_path(params.get("uri", ""))
            with self._cond:
                self.diagnostics[path] = params.get("diagnostics", [])
                self._diag_events[path] = time.monotonic()
                self._cond.notify_all()
        elif method == "language/status":  # jdtls
            msg = f"{params.get('type')}: {params.get('message')}"
            self.status_messages.append(msg)
            if params.get("type") == "ServiceReady":
                self.service_ready = True
            self._touch()
        elif method in ("window/logMessage", "window/showMessage"):
            self.status_messages.append(str((params or {}).get("message", ""))[:300])
        handler = self.notification_handlers.get(method)
        if handler:
            handler(params)

    # ------------------------------------------------------------------ rpc
    def request(self, method: str, params: Any, timeout: float = 30.0) -> Any:
        fut: Future[Any] = Future()
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            self._pending[rid] = fut
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return fut.result(timeout=timeout)
        except FutureTimeout:
            with self._lock:
                self._pending.pop(rid, None)
            try:
                self.notify("$/cancelRequest", {"id": rid})
            except LspError:
                pass
            raise LspTimeout(f"{self.name}: {method} timed out after {timeout}s") from None

    def notify(self, method: str, params: Any) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    # ------------------------------------------------------------------ readiness
    def progress_snapshot(self) -> tuple[int, list[str]]:
        with self._cond:
            return self._progress_seen, list(self._progress_active.values())

    def wait_quiet(
        self,
        timeout: float,
        quiet: float = 1.5,
        require_progress: bool = False,
        require_service_ready: bool = False,
    ) -> bool:
        """Wait until no $/progress is active and nothing happened for `quiet` seconds."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                now = time.monotonic()
                if not self._alive:
                    return False
                done = (
                    not self._progress_active
                    and now - self._last_activity >= quiet
                    and (not require_progress or self._progress_seen > 0)
                    and (not require_service_ready or self.service_ready)
                )
                if done:
                    return True
                if now >= deadline:
                    return False
                self._cond.wait(timeout=min(0.25, deadline - now))

    def wait_progress_title(self, needle: str, timeout: float) -> bool:
        """Wait until a $/progress whose title contains `needle` has begun (or already did)."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                if any(needle in t for t in self.progress_titles):
                    return True
                now = time.monotonic()
                if now >= deadline or not self._alive:
                    return False
                self._cond.wait(timeout=min(0.25, deadline - now))

    def pull_diagnostics(self, path: str | os.PathLike[str], timeout: float = 30.0) -> list[dict[str, Any]] | None:
        """textDocument/diagnostic (LSP 3.17 pull model); None if unsupported."""
        if not self.capabilities.get("diagnosticProvider"):
            return None
        res = self.request("textDocument/diagnostic", {"textDocument": self._td(path)}, timeout)
        if not res:
            return []
        return res.get("items", []) or []

    def wait_diagnostics(self, path: str, since: float, timeout: float) -> list[dict[str, Any]] | None:
        key = norm_path(path)
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                if self._diag_events.get(key, 0) >= since:
                    return self.diagnostics.get(key, [])
                now = time.monotonic()
                if now >= deadline or not self._alive:
                    return None
                self._cond.wait(timeout=min(0.25, deadline - now))

    # ------------------------------------------------------------------ documents
    def open_document(self, path: str | os.PathLike[str], language_id: str, text: str | None = None) -> None:
        key = norm_path(path)
        if text is None:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        uri = path_to_uri(path)
        if key in self._open_versions:
            self._open_versions[key] += 1
            self.notify(
                "textDocument/didChange",
                {"textDocument": {"uri": uri, "version": self._open_versions[key]}, "contentChanges": [{"text": text}]},
            )
            return
        self._open_versions[key] = 1
        self.notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": language_id, "version": 1, "text": text}},
        )

    def open_paths(self) -> list[str]:
        return list(self._open_versions)

    def notify_files_changed(self, changes: list[tuple[str, int]]) -> None:
        """workspace/didChangeWatchedFiles; type 1=created 2=changed 3=deleted."""
        if changes:
            self.notify(
                "workspace/didChangeWatchedFiles",
                {"changes": [{"uri": path_to_uri(p), "type": t} for p, t in changes]},
            )

    def close_document(self, path: str | os.PathLike[str]) -> None:
        key = norm_path(path)
        if self._open_versions.pop(key, None) is not None:
            self.notify("textDocument/didClose", {"textDocument": {"uri": path_to_uri(path)}})

    def is_open(self, path: str | os.PathLike[str]) -> bool:
        return norm_path(path) in self._open_versions

    # ------------------------------------------------------------------ typed helpers
    @staticmethod
    def _td(path: str | os.PathLike[str]) -> dict[str, str]:
        return {"uri": path_to_uri(path)}

    def document_symbols(self, path: str | os.PathLike[str], timeout: float = 30.0) -> list[dict[str, Any]]:
        return self.request("textDocument/documentSymbol", {"textDocument": self._td(path)}, timeout) or []

    def hover(self, path: str | os.PathLike[str], line: int, character: int, timeout: float = 30.0) -> Any:
        return self.request(
            "textDocument/hover",
            {"textDocument": self._td(path), "position": {"line": line, "character": character}},
            timeout,
        )

    def references(
        self, path: str | os.PathLike[str], line: int, character: int, include_declaration: bool = False, timeout: float = 60.0
    ) -> list[dict[str, Any]]:
        return (
            self.request(
                "textDocument/references",
                {
                    "textDocument": self._td(path),
                    "position": {"line": line, "character": character},
                    "context": {"includeDeclaration": include_declaration},
                },
                timeout,
            )
            or []
        )

    def implementation(self, path: str | os.PathLike[str], line: int, character: int, timeout: float = 60.0) -> list[dict[str, Any]]:
        res = self.request(
            "textDocument/implementation",
            {"textDocument": self._td(path), "position": {"line": line, "character": character}},
            timeout,
        )
        if res is None:
            return []
        if isinstance(res, dict):
            res = [res]
        return [{"uri": r.get("targetUri", r.get("uri")), "range": r.get("targetSelectionRange", r.get("range"))} for r in res]

    def definition(self, path: str | os.PathLike[str], line: int, character: int, timeout: float = 30.0) -> list[dict[str, Any]]:
        res = self.request(
            "textDocument/definition",
            {"textDocument": self._td(path), "position": {"line": line, "character": character}},
            timeout,
        )
        if res is None:
            return []
        if isinstance(res, dict):
            res = [res]
        return [{"uri": r.get("targetUri", r.get("uri")), "range": r.get("targetSelectionRange", r.get("range"))} for r in res]

    def prepare_call_hierarchy(self, path: str | os.PathLike[str], line: int, character: int, timeout: float = 60.0) -> list[dict[str, Any]]:
        return (
            self.request(
                "textDocument/prepareCallHierarchy",
                {"textDocument": self._td(path), "position": {"line": line, "character": character}},
                timeout,
            )
            or []
        )

    def incoming_calls(self, item: dict[str, Any], timeout: float = 60.0) -> list[dict[str, Any]]:
        return self.request("callHierarchy/incomingCalls", {"item": item}, timeout) or []

    def prepare_type_hierarchy(self, path: str | os.PathLike[str], line: int, character: int, timeout: float = 60.0) -> list[dict[str, Any]]:
        return (
            self.request(
                "textDocument/prepareTypeHierarchy",
                {"textDocument": self._td(path), "position": {"line": line, "character": character}},
                timeout,
            )
            or []
        )

    def subtypes(self, item: dict[str, Any], timeout: float = 60.0) -> list[dict[str, Any]]:
        return self.request("typeHierarchy/subtypes", {"item": item}, timeout) or []

    def supertypes(self, item: dict[str, Any], timeout: float = 60.0) -> list[dict[str, Any]]:
        return self.request("typeHierarchy/supertypes", {"item": item}, timeout) or []

    def supports(self, capability: str) -> bool:
        v = self.capabilities.get(capability)
        return bool(v)
