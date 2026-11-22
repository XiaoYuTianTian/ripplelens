"""Fixture repos with a known edit and the impact we expect ripplelens to report.

Each scenario copies tests/fixtures/<lang> into a temp git repo, commits it,
applies `edits` (path -> (old, new) text replacements) to the working tree, and
lists the expected changed entities and impacted entity names.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@dataclass
class Scenario:
    name: str
    lang: str
    edits: dict[str, list[tuple[str, str]]]
    changed: dict[str, str]  # short entity name -> change class
    impacted: set[str]  # short names that must be impacted
    not_impacted: set[str] = field(default_factory=set)
    breaking: set[str] = field(default_factory=set)  # short names with compile errors
    compile: list[str] | None = None
    compiler: str | None = None


SCENARIOS = [
    Scenario(
        "python-signature",
        "python",
        {"fx/lib.py": [("def helper(x: int) -> int:\n    return x + LIMIT", "def helper(x: int, y: int) -> int:\n    return x + y + LIMIT")]},
        changed={"helper": "signature"},
        impacted={"a", "b", "c"},
        not_impacted={"d", "e", "total"},
        breaking={"a", "b"},
    ),
    Scenario(
        "python-override-body",
        "python",
        {"fx/lib.py": [("return 3.14 * self.r * self.r", "return 3.14159 * self.r * self.r")]},
        changed={"area": "body"},
        impacted={"d", "total", "e"},
        not_impacted={"a", "b", "c"},
    ),
    Scenario(
        "ts-signature",
        "ts",
        {"src/lib.ts": [("export function helper(x: number): number {\n  return x + LIMIT;", "export function helper(x: number, y: number): number {\n  return x + y + LIMIT;")]},
        changed={"helper": "signature"},
        impacted={"a", "b", "c"},
        not_impacted={"d", "e", "total"},
        breaking={"a", "b", "c"},
    ),
    Scenario(
        "ts-constant",
        "ts",
        {"src/lib.ts": [("export const LIMIT = 10;", "export const LIMIT = 20;")]},
        changed={"LIMIT": "value"},
        impacted={"helper", "a", "b"},
        not_impacted={"d", "total"},
    ),
    Scenario(
        "python-cosmetic",
        "python",
        {"fx/lib.py": [("def helper(x: int) -> int:\n    return x + LIMIT", "def helper(x: int) -> int:\n    return x  +  LIMIT   ")]},
        changed={"helper": "cosmetic"},
        impacted=set(),
        not_impacted={"a", "b", "c"},
    ),
    Scenario(
        "ts-renamed",
        "ts",
        {"src/lib.ts": [("export function helper(x: number): number {", "export function helperV2(x: number): number {")]},
        changed={"helperV2": "renamed"},
        impacted={"a", "b", "c"},
        not_impacted={"d", "total"},
    ),
    Scenario(
        "go-signature",
        "go",
        {"lib/lib.go": [("func Helper(x int) int {\n\treturn x + Limit", "func Helper(x, y int) int {\n\treturn x + y + Limit")]},
        changed={"helper": "signature"},
        impacted={"a", "b", "c"},
        not_impacted={"d", "e", "total"},
        breaking={"a", "b", "c"},
    ),
    Scenario(
        "go-interface-impl-body",
        "go",
        {"lib/lib.go": [("return 3.14 * c.R * c.R", "return 3.14159 * c.R * c.R")]},
        changed={"area": "body"},
        impacted={"d", "total", "e"},
        not_impacted={"a", "b", "c"},
    ),
    Scenario(
        "go-removed",
        "go",
        {"lib/lib.go": [("func Helper(x int) int {\n\treturn x + Limit\n}\n", "")]},
        changed={"helper": "removed"},
        impacted={"a", "b", "c"},
        not_impacted={"d", "total"},
    ),
    Scenario(
        "cpp-virtual-body",
        "cpp",
        {"lib.cpp": [("return 3.14 * r_ * r_;", "return 3.14159 * r_ * r_;")]},
        changed={"area": "body"},
        impacted={"d", "total", "e"},
        not_impacted={"a", "b", "c"},
        compile=["lib.cpp", "app.cpp"],
        compiler="clang++",
    ),
    Scenario(
        "c-signature",
        "c",
        {
            "lib.c": [("int helper(int x) {\n    return x + limit_value;", "int helper(int x, int y) {\n    return x + y + limit_value;")],
            "lib.h": [("int helper(int x);", "int helper(int x, int y);")],
        },
        changed={"helper": "signature"},
        impacted={"a", "b", "c"},
        not_impacted={"d", "e"},
        breaking={"a", "b"},
        compile=["lib.c", "app.c"],
        compiler="clang",
    ),
    Scenario(
        "java-interface-signature",
        "java",
        {
            "src/main/java/fx/lib/Shape.java": [("double area();", "double area(double scale);")],
        },
        changed={"area": "signature"},
        impacted={"total", "area"},
        not_impacted={"a", "b", "c"},
        breaking={"total", "area"},
    ),
]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def rmtree(path: Path) -> None:
    def onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onerror=onerror)


def materialize(sc: Scenario, dest: Path) -> Path:
    rmtree(dest)
    shutil.copytree(FIXTURES / sc.lang, dest)
    dest = dest.resolve()  # long path: clangd ignores compile commands under 8.3 short paths
    if sc.compile:
        std = "-std=c11" if sc.compiler == "clang" else "-std=c++17"
        cc = [{"directory": str(dest), "file": str(dest / f), "arguments": [sc.compiler, std, "-c", f]} for f in sc.compile]
        (dest / "compile_commands.json").write_text(json.dumps(cc, indent=2), encoding="utf-8")
        (dest / ".gitignore").write_text(".cache/\n", encoding="utf-8")
    git(dest, "init", "-q")
    git(dest, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(dest, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false", "commit", "-q", "-m", "base")
    for rel, reps in sc.edits.items():
        p = dest / rel
        text = p.read_text(encoding="utf-8")
        for old, new in reps:
            assert old in text, f"{sc.name}: edit anchor not found in {rel}: {old!r}"
            text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8", newline="\n")
    return dest


def short(name: str) -> str:
    n = name.split("(")[0]
    for sep in ("::", "."):
        n = n.split(sep)[-1]
    return n.lower()


def check(sc: Scenario, rep: dict) -> list[str]:
    """Return a list of failures (empty = pass)."""
    fails = []
    changed = {short(n["name"]): n["change"] for n in rep["nodes"] if n["change"]}
    for name, cls in sc.changed.items():
        got = changed.get(name.lower())
        if got != cls:
            fails.append(f"changed {name}: expected {cls}, got {got}")
    impacted = {short(n["name"]) for n in rep["nodes"] if n["change"] is None}
    missing = {x.lower() for x in sc.impacted} - impacted
    if missing:
        fails.append(f"missing impacted: {sorted(missing)} (got {sorted(impacted)})")
    extra = {x.lower() for x in sc.not_impacted} & impacted
    if extra:
        fails.append(f"false positives: {sorted(extra)}")
    breaking = {short(n["name"]) for n in rep["nodes"] if n["breaking"]}
    miss_b = {x.lower() for x in sc.breaking} - breaking
    if miss_b:
        fails.append(f"missing compile errors at: {sorted(miss_b)} (got {sorted(breaking)})")
    return fails
