"""Language server registry.

Entries use the same shape as Claude Code's `.lsp.json` (`command`, `args`,
`extensionToLanguage`, `env`, `initializationOptions`, `settings`,
`startupTimeout`) plus a few ripplelens-only keys under `ripplelens`:

  readiness:  "progress" | "service_ready"   how to decide the index is warm
  install:    {"windows": str, "linux": str}  hint printed by `ripplelens doctor`
  needs:      list of project files the server needs for precise answers
              (e.g. compile_commands.json for clangd)

A user/project `.lsp.json` (or `.ripplelens/lsp.json`) overrides builtins by name.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IS_WINDOWS = sys.platform == "win32"


def cache_dir() -> Path:
    base = os.environ.get("RIPPLELENS_CACHE")
    if base:
        return Path(base)
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ripplelens"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ripplelens"


def tools_dirs() -> list[Path]:
    """Extra dirs searched for server binaries (before PATH)."""
    dirs: list[Path] = []
    extra = os.environ.get("RIPPLELENS_TOOLS")
    if extra:
        dirs.extend(Path(p) for p in extra.split(os.pathsep) if p)
    dirs.append(cache_dir() / "bin")
    return dirs


BUILTIN_SERVERS: dict[str, dict[str, Any]] = {
    "gopls": {
        "command": "gopls",
        "args": [],
        "extensionToLanguage": {".go": "go"},
        "startupTimeout": 180,
        "ripplelens": {
            "readiness": "progress",
            "install": {
                "windows": "go install golang.org/x/tools/gopls@latest",
                "linux": "go install golang.org/x/tools/gopls@latest",
            },
        },
    },
    "typescript": {
        "command": "typescript-language-server",
        "args": ["--stdio"],
        "extensionToLanguage": {
            ".ts": "typescript",
            ".mts": "typescript",
            ".cts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".jsx": "javascriptreact",
        },
        "startupTimeout": 180,
        "initializationOptions": {"preferences": {"includeCompletionsForModuleExports": False}},
        "ripplelens": {
            "readiness": "progress",
            "install": {
                "windows": "npm install -g typescript typescript-language-server",
                "linux": "npm install -g typescript typescript-language-server",
            },
            "needs": ["tsconfig.json|jsconfig.json|package.json"],
        },
    },
    "pyright": {
        "command": "basedpyright-langserver",
        "fallbackCommands": ["pyright-langserver"],
        "args": ["--stdio"],
        "extensionToLanguage": {".py": "python", ".pyi": "python"},
        "startupTimeout": 180,
        "settings": {
            "basedpyright": {"analysis": {"diagnosticMode": "openFilesOnly", "typeCheckingMode": "standard"}},
            "python": {"analysis": {"diagnosticMode": "openFilesOnly", "typeCheckingMode": "standard"}},
        },
        "ripplelens": {
            "readiness": "progress",
            "install": {"windows": "pip install basedpyright", "linux": "pip install basedpyright"},
        },
    },
    "clangd": {
        "command": "clangd",
        "args": ["--background-index", "--header-insertion=never", "--log=error", "--pch-storage=memory"],
        "extensionToLanguage": {
            ".c": "c",
            ".h": "cpp",
            ".cc": "cpp",
            ".cpp": "cpp",
            ".cxx": "cpp",
            ".c++": "cpp",
            ".hpp": "cpp",
            ".hh": "cpp",
            ".hxx": "cpp",
            ".ipp": "cpp",
            ".inl": "cpp",
        },
        "startupTimeout": 600,
        "initializationOptions": {"clangdFileStatus": True},
        "ripplelens": {
            "readiness": "progress",
            "indexProgress": "index",
            "install": {
                "windows": "winget install LLVM.LLVM  (or download clangd from https://github.com/clangd/clangd/releases)",
                "linux": "apt install clangd  (or download from https://github.com/clangd/clangd/releases)",
            },
            "needs": ["compile_commands.json"],
        },
    },
    "jdtls": {
        "command": "jdtls",
        "args": ["-data", "${WORKSPACE_DATA}"],
        "extensionToLanguage": {".java": "java"},
        "startupTimeout": 240,
        "initializationOptions": {
            "settings": {
                "java": {
                    "import": {"gradle": {"enabled": True}, "maven": {"enabled": True}},
                    "autobuild": {"enabled": True},
                    "references": {"includeDecompiledSources": False},
                }
            },
            "extendedClientCapabilities": {"progressReportProvider": True, "classFileContentsSupport": False},
        },
        "ripplelens": {
            "readiness": "service_ready",
            "install": {
                "windows": "JDK 25 (21 for older jdtls) and https://download.eclipse.org/jdtls/snapshots/ (put bin/ on PATH or set JDTLS_HOME)",
                "linux": "JDK 25 (21 for older jdtls) and https://download.eclipse.org/jdtls/snapshots/ (put bin/ on PATH or set JDTLS_HOME)",
            },
            "needs": ["pom.xml|build.gradle|build.gradle.kts"],
        },
    },
}

LANGUAGE_OF_SERVER = {"gopls": "go", "typescript": "typescript", "pyright": "python", "clangd": "c/c++", "jdtls": "java"}


@dataclass
class ServerSpec:
    name: str
    command: str
    args: list[str]
    extension_to_language: dict[str, str]
    env: dict[str, str] = field(default_factory=dict)
    initialization_options: Any = None
    settings: dict[str, Any] = field(default_factory=dict)
    startup_timeout: float = 180.0
    readiness: str = "progress"
    install: dict[str, str] = field(default_factory=dict)
    needs: list[str] = field(default_factory=list)
    fallback_commands: list[str] = field(default_factory=list)
    index_progress: str | None = None

    @classmethod
    def from_entry(cls, name: str, entry: dict[str, Any]) -> ServerSpec:
        extra = entry.get("ripplelens", {}) or {}
        return cls(
            name=name,
            command=entry["command"],
            args=list(entry.get("args", [])),
            extension_to_language=dict(entry.get("extensionToLanguage", {})),
            env=dict(entry.get("env", {}) or {}),
            initialization_options=entry.get("initializationOptions"),
            settings=dict(entry.get("settings", {}) or {}),
            startup_timeout=float(entry.get("startupTimeout", 180)),
            readiness=extra.get("readiness", "progress"),
            install=dict(extra.get("install", {})),
            needs=list(extra.get("needs", [])),
            fallback_commands=list(entry.get("fallbackCommands", [])),
            index_progress=extra.get("indexProgress"),
        )

    def resolve_executable(self) -> str | None:
        for cmd in [self.command, *self.fallback_commands]:
            found = which(cmd)
            if found:
                return found
        if self.name == "jdtls":
            home = os.environ.get("JDTLS_HOME")
            if home:
                launcher = Path(home) / "bin" / "jdtls"
                if launcher.exists():
                    return str(launcher)
        return None

    def build_command(self, root: Path) -> list[str] | None:
        if self.name == "typescript" and self.command == "typescript-language-server":
            native = find_native_tsc(root, self.resolve_executable())
            if native:
                return [*native, "--lsp", "--stdio"]
        exe = self.resolve_executable()
        if not exe:
            return None
        ws = cache_dir() / "jdtls-data" / hashlib.sha1(str(root).lower().encode()).hexdigest()[:16]
        args = [a.replace("${WORKSPACE_DATA}", str(ws)).replace("${ROOT}", str(root)) for a in self.args]
        cmd = [exe, *args]
        if self.name == "jdtls":
            # bin/jdtls is a python launcher; jdtls.bat calls whatever `python` is on PATH and then
            # `pause`, which reads the LSP stdin. Run the launcher with the current interpreter.
            launcher = Path(exe)
            if launcher.suffix.lower() in (".bat", ".cmd"):
                launcher = launcher.with_suffix("")
            if launcher.suffix.lower() != ".exe" and launcher.is_file():
                cmd = [sys.executable, str(launcher), *args]
        return cmd

    def init_options_for(self, root: Path) -> Any:
        opts = json.loads(json.dumps(self.initialization_options)) if self.initialization_options else None
        if self.name == "typescript":
            opts = opts or {}
            ts = opts.setdefault("tsserver", {})
            if not ts.get("path"):
                found = find_tsserver(root, self.resolve_executable())
                if found:
                    ts["path"] = found
        return opts

    def language_id(self, path: str) -> str | None:
        return self.extension_to_language.get(Path(path).suffix.lower())


def ts_project_roots(root: Path, limit: int = 24) -> list[Path]:
    """Directories that look like TypeScript project roots (nearest ts/jsconfig)."""
    found: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            found.append(p)

    if (root / "tsconfig.json").is_file() or (root / "jsconfig.json").is_file() or (root / "package.json").is_file():
        add(root)
    try:
        for cfg in sorted(root.rglob("tsconfig.json")):
            if len(found) >= limit:
                break
            if any(x in cfg.parts for x in ("node_modules", "dist", "build", ".git")):
                continue
            add(cfg.parent)
        for cfg in sorted(root.rglob("jsconfig.json")):
            if len(found) >= limit:
                break
            if any(x in cfg.parts for x in ("node_modules", "dist", "build", ".git")):
                continue
            add(cfg.parent)
    except OSError:
        pass
    return found


def typescript_package_version(pkg: Path) -> str | None:
    manifest = pkg / "package.json"
    if not manifest.is_file():
        return None
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, json.JSONDecodeError):
        return None


def _ts_package_dirs(root: Path, server_exe: str | None) -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()
    for pr in ts_project_roots(root):
        d = pr / "node_modules" / "typescript"
        k = str(d).lower()
        if k not in seen:
            dirs.append(d)
            seen.add(k)
    d0 = root / "node_modules" / "typescript"
    if str(d0).lower() not in seen:
        dirs.insert(0, d0)
    if server_exe:
        exe = Path(server_exe).resolve()
        dirs += [
            exe.parent.parent / "typescript",
            exe.parent.parent / "lib" / "node_modules" / "typescript",
            exe.parent / "node_modules" / "typescript",
        ]
    for d in tools_dirs():
        dirs += [d.parent / "typescript", d / "node_modules" / "typescript"]
    return dirs


def find_native_tsc(root: Path, server_exe: str | None) -> list[str] | None:
    """TypeScript >= 7 (the Go port) ships its own LSP: `tsc --lsp --stdio`.

    The project's own TypeScript decides: a project pinned to TS 5/6 keeps tsserver.
    """
    for pkg in _ts_package_dirs(root, server_exe):
        manifest = pkg / "package.json"
        if not manifest.is_file():
            continue
        try:
            version = json.loads(manifest.read_text(encoding="utf-8")).get("version", "0")
        except (OSError, json.JSONDecodeError):
            continue
        if int(version.split(".")[0] or 0) < 7:
            return None
        launcher = pkg / "bin" / "tsc"
        node = shutil.which("node")
        if launcher.is_file() and node:
            return [node, str(launcher)]
    return None


def find_tsserver(root: Path, server_exe: str | None) -> str | None:
    """Prefer the project's own TypeScript, then the one installed next to the server."""
    candidates: list[Path] = []
    for pr in ts_project_roots(root):
        candidates.append(pr / "node_modules" / "typescript" / "lib")
    candidates.append(root / "node_modules" / "typescript" / "lib")
    if server_exe:
        exe = Path(server_exe).resolve()
        # <prefix>/node_modules/.bin/typescript-language-server(.cmd)
        candidates.append(exe.parent.parent / "typescript" / "lib")
        # <prefix>/bin/typescript-language-server (global npm on linux)
        candidates.append(exe.parent.parent / "lib" / "node_modules" / "typescript" / "lib")
        # %APPDATA%/npm/typescript-language-server.cmd (global npm on windows)
        candidates.append(exe.parent / "node_modules" / "typescript" / "lib")
    for c in candidates:
        if (c / "tsserver.js").is_file():
            return str(c / "tsserver.js")
    return None


def which(cmd: str) -> str | None:
    if os.path.isabs(cmd) and os.path.exists(cmd):
        return cmd
    search = os.pathsep.join([str(d) for d in tools_dirs()] + [os.environ.get("PATH", "")])
    return shutil.which(cmd, path=search)


class Registry:
    def __init__(self, servers: dict[str, ServerSpec]):
        self.servers = servers
        self._by_ext: dict[str, ServerSpec] = {}
        for spec in servers.values():
            for ext in spec.extension_to_language:
                self._by_ext.setdefault(ext.lower(), spec)

    @classmethod
    def load(cls, project_root: str | os.PathLike[str] | None = None) -> Registry:
        entries: dict[str, dict[str, Any]] = json.loads(json.dumps(BUILTIN_SERVERS))
        candidates: list[Path] = [cache_dir() / "lsp.json"]
        if project_root:
            root = Path(project_root)
            candidates += [root / ".lsp.json", root / ".ripplelens" / "lsp.json"]
        for path in candidates:
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for name, entry in data.items():
                    if isinstance(entry, dict) and "command" in entry:
                        merged = {**entries.get(name, {}), **entry}
                        entries[name] = merged
        return cls({name: ServerSpec.from_entry(name, e) for name, e in entries.items()})

    def for_path(self, path: str) -> ServerSpec | None:
        return self._by_ext.get(Path(path).suffix.lower())

    def supported_extensions(self) -> set[str]:
        return set(self._by_ext)
