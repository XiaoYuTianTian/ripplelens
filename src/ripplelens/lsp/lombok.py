"""Detect Lombok usage and whether jdtls is configured with the annotation agent."""

from __future__ import annotations

import json
import os
from pathlib import Path


def repo_uses_lombok(repo: Path) -> bool:
    for name in ("pom.xml", "build.gradle", "build.gradle.kts"):
        p = repo / name
        if p.is_file() and "lombok" in p.read_text(encoding="utf-8", errors="replace").lower():
            return True
    for p in repo.rglob("pom.xml"):
        if "node_modules" in p.parts or len(p.relative_to(repo).parts) > 6:
            continue
        try:
            if "lombok" in p.read_text(encoding="utf-8", errors="replace").lower():
                return True
        except OSError:
            continue
    return False


def find_lombok_jar() -> Path | None:
    explicit = os.environ.get("LOMBOK_JAR") or os.environ.get("RIPPLELENS_LOMBOK_JAR")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    m2 = Path.home() / ".m2" / "repository" / "org" / "projectlombok" / "lombok"
    if m2.is_dir():
        jars = sorted(m2.glob("*/lombok-*.jar"), key=lambda p: p.stat().st_mtime, reverse=True)
        if jars:
            return jars[0]
    return None


def jdtls_has_lombok_agent(repo: Path) -> bool:
    for rel in (".lsp.json", ".ripplelens/lsp.json"):
        p = repo / rel
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        block = data.get("jdtls") or data.get("servers", {}).get("jdtls") or data
        blob = json.dumps(block, ensure_ascii=False).lower()
        if "lombok" in blob and "javaagent" in blob:
            return True
    return False


def lombok_agent_vmarg(jar: Path) -> str:
    return f"-javaagent:{jar.resolve()}"


def lombok_lsp_snippet(jar: Path) -> str:
    arg = lombok_agent_vmarg(jar)
    return json.dumps(
        {
            "jdtls": {
                "settings": {
                    "java.jdt.ls.vmargs": arg,
                }
            }
        },
        indent=2,
        ensure_ascii=False,
    )