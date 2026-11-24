"""`ripplelens doctor`: which languages are in the repo, which servers are usable, what is missing."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .lsp.pool import ServerPool, ServerUnavailable
from .lsp import lombok as lombok_help
from .lsp.registry import (
    IS_WINDOWS,
    LANGUAGE_OF_SERVER,
    Registry,
    ServerSpec,
    find_native_tsc,
    find_tsserver,
    ts_project_roots,
    typescript_package_version,
)


def _exe_version(exe: str) -> str | None:
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    return text[0] if text else None


def _repo_files(repo: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=str(repo), capture_output=True, check=True
        ).stdout.decode("utf-8", "replace")
        return [f for f in out.split("\0") if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _java_major() -> int | None:
    java = shutil.which("java")
    if not java:
        home = os.environ.get("JAVA_HOME")
        if home and (Path(home) / "bin" / ("java.exe" if IS_WINDOWS else "java")).exists():
            java = str(Path(home) / "bin" / "java")
    if not java:
        return None
    proc = subprocess.run([java, "-version"], capture_output=True)
    m = re.search(r'version "(\d+)(?:\.(\d+))?', proc.stderr.decode("utf-8", "replace"))
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major


def _check_project(spec: ServerSpec, repo: Path) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []

    def add(level: str, msg: str) -> None:
        notes.append({"level": level, "message": msg})

    if spec.name == "clangd":
        dbs = [repo / d / "compile_commands.json" for d in ("", "build", "out", "cmake-build-debug")]
        db = next((d for d in dbs if d.exists()), None)
        if db is None:
            add("error", "compile_commands.json not found (repo root or build/). Generate it, e.g. "
                         "`cmake -B build -G Ninja -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` or `bear -- make`. "
                         "Without it clangd cannot index other files and callers will be missed.")
        else:
            try:
                entries = json.loads(db.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                add("error", f"{db} is not valid JSON: {e}")
                entries = []
            bad = 0
            for e in entries[:200]:
                d = e.get("directory", "")
                if d and os.path.isdir(d) and os.path.normcase(str(Path(d).resolve())) != os.path.normcase(os.path.normpath(d)):
                    bad += 1
            if bad:
                add("warn", f"{bad} compile commands use a non-canonical directory path (8.3 short name or symlink); "
                            "clangd's background index skips them. Regenerate from the canonical path.")
            add("ok", f"compile_commands.json: {len(entries)} entries ({db.relative_to(repo)})")
        if IS_WINDOWS and not (shutil.which("cl") or shutil.which("clang") or shutil.which("gcc")
                               or Path("C:/Program Files/Microsoft Visual Studio").exists()
                               or Path("C:/Program Files (x86)/Microsoft Visual Studio").exists()):
            add("warn", "no MSVC/clang/gcc toolchain found: system headers are unavailable to clangd; "
                        "code that includes standard headers will be partially indexed")
    elif spec.name == "jdtls":
        major = _java_major()
        if major is None:
            add("error", "java not found (jdtls needs a recent JDK on PATH or JAVA_HOME: 21+ for jdtls 1.x, 25+ for late-2026 builds)")
        elif major < 21:
            add("error", f"java {major} found; jdtls needs JDK 21+ to run (projects may target older releases)")
        elif major < 25:
            add("warn", f"java {major}: enough for jdtls releases up to mid-2026; newer jdtls builds need JDK 25")
        else:
            add("ok", f"java {major}")
        if not any((repo / f).exists() for f in ("pom.xml", "build.gradle", "build.gradle.kts")):
            add("warn", "no pom.xml/build.gradle at the repo root; jdtls treats sources as invisible project")
        if lombok_help.repo_uses_lombok(repo):
            if lombok_help.jdtls_has_lombok_agent(repo):
                add("ok", "Lombok: javaagent configured for jdtls (.lsp.json)")
            else:
                jar = lombok_help.find_lombok_jar()
                if jar:
                    add(
                        "error",
                        "Lombok on the classpath but jdtls has no Lombok javaagent — diagnostics miss "
                        f"generated getters/setters. Add to `.lsp.json`: settings.java.jdt.ls.vmargs = "
                        f'"{lombok_help.lombok_agent_vmarg(jar)}"',
                    )
                else:
                    add("warn", "Lombok dependency detected but no lombok.jar found under ~/.m2; set LOMBOK_JAR")
    elif spec.name == "typescript":
        native = find_native_tsc(repo, spec.resolve_executable())
        if native:
            add("ok", "TypeScript >= 7: using the native `tsc --lsp` server")
        else:
            ts = find_tsserver(repo, spec.resolve_executable())
            if ts:
                add("ok", f"tsserver: {ts}")
            else:
                add("error", "no TypeScript found (project node_modules or global); `npm i -D typescript`")
        roots = ts_project_roots(repo)
        if roots:
            bits = []
            for pr in roots[:5]:
                ver = typescript_package_version(pr / "node_modules" / "typescript")
                rel = pr.relative_to(repo) if pr != repo else Path(".")
                bits.append(f"{rel}" + (f" (typescript {ver})" if ver else ""))
            add("ok", "TypeScript projects: " + "; ".join(bits))
        if not any((repo / f).exists() for f in ("tsconfig.json", "jsconfig.json")):
            if roots and roots != [repo]:
                add("info", "no tsconfig at repo root; using nested project(s) above")
            else:
                add("warn", "no tsconfig.json/jsconfig.json at the repo root: files outside a project get an inferred "
                            "project and cross-file results may be incomplete")
        global_lib = spec.resolve_executable()
        if global_lib and roots:
            gts = find_tsserver(repo, global_lib)
            for pr in roots[:3]:
                local = pr / "node_modules" / "typescript" / "lib" / "tsserver.js"
                if local.is_file() and gts and str(local.resolve()) != str(Path(gts).resolve()):
                    lv = typescript_package_version(pr / "node_modules" / "typescript")
                    add("warn", f"nested project {pr.relative_to(repo) if pr != repo else '.'} pins TypeScript {lv}; "
                                "ensure tsserver resolves to that copy (not only a global install)")
                    break
    elif spec.name == "pyright":
        if not any((repo / f).exists() for f in ("pyrightconfig.json", "pyproject.toml")):
            add("info", "no pyrightconfig.json/pyproject.toml; pyright uses defaults")
        venvs = [d for d in (".venv", "venv", "env") if (repo / d).is_dir()]
        if venvs:
            add("info", f"virtualenv {venvs[0]}/ found; pyright resolves third-party imports from it when configured "
                        "(`venvPath`/`venv` in pyrightconfig.json)")
    elif spec.name == "gopls":
        if not any((repo / f).exists() for f in ("go.mod", "go.work")):
            mods = [p for p in repo.glob("*/go.mod")]
            if mods:
                add("warn", f"no go.mod/go.work at the root but {len(mods)} nested modules; add a go.work so gopls loads them all")
            else:
                add("warn", "no go.mod at the repo root")
        if not shutil.which("go"):
            add("error", "go toolchain not on PATH (gopls needs it to load packages)")
    return notes


def run_doctor(repo: str, as_json: bool = False, start: bool = True) -> int:
    root = Path(repo).resolve()
    reg = Registry.load(root)
    files = _repo_files(root)
    ext_counts = Counter(Path(f).suffix.lower() for f in files)
    report: dict[str, Any] = {"repo": str(root), "platform": sys.platform, "servers": []}
    pool = ServerPool(reg) if start else None
    worst = 0
    try:
        for name, spec in reg.servers.items():
            n_files = sum(c for e, c in ext_counts.items() if e in spec.extension_to_language)
            exe = spec.resolve_executable()
            if spec.name == "typescript" and not exe:
                native = find_native_tsc(root, None)
                exe = native[-1] if native else None
            exe_ver = _exe_version(exe) if exe else None
            entry: dict[str, Any] = {
                "server": name,
                "language": LANGUAGE_OF_SERVER.get(name, name),
                "repo_files": n_files,
                "executable": exe,
                "executable_version": exe_ver,
                "install": spec.install.get("windows" if IS_WINDOWS else "linux"),
                "notes": _check_project(spec, root) if n_files else [],
            }
            if exe and start and n_files and pool is not None:
                try:
                    h = pool.get(spec, root)
                    entry["version"] = h.version or (h.client.server_info or {}).get("name")
                    caps = h.client.capabilities
                    entry["capabilities"] = {
                        "callHierarchy": bool(caps.get("callHierarchyProvider")),
                        "references": bool(caps.get("referencesProvider")),
                        "implementation": bool(caps.get("implementationProvider")),
                        "typeHierarchy": bool(caps.get("typeHierarchyProvider")),
                    }
                    entry["status"] = "ok"
                except ServerUnavailable as e:
                    entry["status"] = "error"
                    entry["error"] = e.reason
            else:
                entry["status"] = "found" if exe else "missing"
            if n_files:
                if entry["status"] in ("missing", "error") or any(x["level"] == "error" for x in entry["notes"]):
                    worst = max(worst, 1)
            report["servers"].append(entry)
    finally:
        if pool:
            pool.shutdown()

    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return worst
    print(f"ripplelens doctor — {root}")
    for e in report["servers"]:
        mark = {"ok": "✓", "found": "✓", "missing": "✗", "error": "✗"}[e["status"]]
        used = f"{e['repo_files']} files" if e["repo_files"] else "not used in this repo"
        ver = f" {e.get('version')}" if e.get("version") else ""
        print(f"{mark} {e['language']:<11} {e['server']:<11} {used:<22}{ver}")
        if e.get("executable"):
            extra = f" ({e['executable_version']})" if e.get("executable_version") else ""
            print(f"    binary: {e['executable']}{extra}")
        if e["status"] == "missing" and e["repo_files"]:
            print(f"    install: {e['install']}")
        if e.get("error"):
            print(f"    error: {e['error']}")
        caps = e.get("capabilities")
        if caps and not caps.get("callHierarchy"):
            print("    warn: server has no call hierarchy; callers come from references only")
        for note in e["notes"]:
            sym = {"ok": "·", "info": "·", "warn": "!", "error": "✗"}[note["level"]]
            print(f"    {sym} {note['message']}")
    return worst
