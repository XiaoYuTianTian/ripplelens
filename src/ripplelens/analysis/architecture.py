"""Module/package-level evidence for an architecture review of a diff.

Everything here is computed, not guessed:

deps_added / deps_removed  import lines that appear only in the head (or only in the base) version of a
                           changed file, resolved to the file, module and package they point at
contracts                  contract changes (signature, removed, renamed, value, type) and the other
                           modules their callers live in
cycles                     module cycles that contain at least one added dependency
violations                 added dependencies that break `.ripplelens/architecture.toml` (or .json)

Without a rules file only the facts are reported and `rules.source` is null.
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .grouping import Grouper

CONTRACT = ("signature", "removed", "renamed", "value", "type")
RULE_FILES = (".ripplelens/architecture.toml", ".ripplelens/architecture.json")
SCAN_BUDGET = 3000  # unchanged source files read while looking for a dependency path back (cycle check)
MAX_MODULES = 300

_TS_EXT = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")
_LANG = {
    ".go": "go", ".py": "python", ".pyi": "python", ".java": "java", ".kt": "java",
    ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".cxx": "c", ".hpp": "c", ".hh": "c", ".hxx": "c",
}
for _e in _TS_EXT:
    _LANG[_e] = "ts"


@dataclass(frozen=True)
class Import:
    spec: str
    line: int  # 1-based
    text: str


# ---------------------------------------------------------------------------- import extraction

_TS_PATTERNS = (
    re.compile(r"""^\s*import\s+(?:type\s+)?(?:[\w*{}\s,$]+\s+from\s+)?['"]([^'"]+)['"]"""),
    re.compile(r"""^\s*export\s+(?:type\s+)?[\w*{}\s,$]*\s+from\s+['"]([^'"]+)['"]"""),
    re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)"""),
)
_TS_FROM_CONT = re.compile(r"""^\s*}\s*from\s+['"]([^'"]+)['"]""")
_PY_FROM = re.compile(r"^\s*from\s+(\.*[\w.]*)\s+import\s+(.+)")
_PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)")
_GO_SINGLE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"')
_GO_BLOCK_ITEM = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"')
_JAVA = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;")
_C_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"')


def extract_imports(lang: str, text: str) -> list[Import]:
    out: list[Import] = []
    lines = text.splitlines()
    if lang == "ts":
        for i, ln in enumerate(lines):
            for pat in (*_TS_PATTERNS, _TS_FROM_CONT):
                for m in pat.finditer(ln):
                    out.append(Import(m.group(1), i + 1, ln.strip()))
    elif lang == "python":
        for i, ln in enumerate(lines):
            m = _PY_FROM.match(ln)
            if m:
                mod, names = m.group(1), m.group(2)
                if set(mod) == {"."}:
                    for nm in re.findall(r"\w+", names.split("#")[0]):
                        out.append(Import(f"{mod}{nm}", i + 1, ln.strip()))
                else:
                    out.append(Import(mod, i + 1, ln.strip()))
                continue
            m = _PY_IMPORT.match(ln)
            if m:
                for part in m.group(1).split(","):
                    out.append(Import(part.split(" as ")[0].strip(), i + 1, ln.strip()))
    elif lang == "go":
        block = False
        for i, ln in enumerate(lines):
            s = ln.strip()
            if block:
                if s.startswith(")"):
                    block = False
                    continue
                m = _GO_BLOCK_ITEM.match(ln)
                if m:
                    out.append(Import(m.group(1), i + 1, s))
                continue
            if re.match(r"^import\s*\($", s):
                block = True
                continue
            m = _GO_SINGLE.match(ln)
            if m:
                out.append(Import(m.group(1), i + 1, s))
    elif lang == "java":
        for i, ln in enumerate(lines):
            m = _JAVA.match(ln)
            if m:
                out.append(Import(m.group(1), i + 1, ln.strip()))
    elif lang == "c":
        for i, ln in enumerate(lines):
            m = _C_INCLUDE.match(ln)
            if m:
                out.append(Import(m.group(1), i + 1, ln.strip()))
    return out


# ---------------------------------------------------------------------------- resolution


class Resolver:
    """Map an import spec in a file to a repo file (or an external package name)."""

    def __init__(self, root: Path, files: Iterable[str]):
        self.root = root
        self.files = set(files)
        self.by_suffix: dict[str, list[str]] = {}
        for f in self.files:
            self.by_suffix.setdefault(PurePosixPath(f).name, []).append(f)
        self.npm: dict[str, str] = {}
        self.gomod: dict[str, str] = {}
        for f in self.files:
            name = PurePosixPath(f).name
            d = posixpath.dirname(f)
            if name == "package.json":
                try:
                    pkg = json.loads((root / f).read_text(encoding="utf-8"))
                    if isinstance(pkg, dict) and isinstance(pkg.get("name"), str) and d:
                        self.npm[pkg["name"]] = d
                except (OSError, ValueError):
                    pass
            elif name == "go.mod":
                try:
                    for ln in (root / f).read_text(encoding="utf-8").splitlines():
                        if ln.startswith("module "):
                            self.gomod[ln.split()[1].strip()] = d
                            break
                except OSError:
                    pass
        self.java: dict[str, str] = {}
        for f in self.files:
            if f.endswith((".java", ".kt")):
                for r in ("src/main/java/", "src/test/java/", "src/main/kotlin/", "src/test/kotlin/"):
                    i = f.find(r)
                    if i >= 0:
                        self.java[f[i + len(r) :].rsplit(".", 1)[0].replace("/", ".")] = f
                        break

    def _first(self, cands: Iterable[str]) -> str | None:
        for c in cands:
            c = posixpath.normpath(c)
            if c in self.files:
                return c
        return None

    def _dir_hit(self, d: str) -> str | None:
        d = posixpath.normpath(d)
        prefix = d + "/"
        hits = sorted(f for f in self.files if f.startswith(prefix))
        return hits[0] if hits else None

    def resolve(self, lang: str, src: str, spec: str) -> tuple[str | None, str | None]:
        """(repo file, external name). Exactly one is set, or neither for stdlib/system imports."""
        here = posixpath.dirname(src)
        if lang == "ts":
            if spec.startswith("."):
                base = posixpath.join(here, spec)
                hit = self._first([base, *(base + e for e in _TS_EXT), *(posixpath.join(base, "index" + e) for e in _TS_EXT)])
                return hit, None
            parts = spec.split("/")
            name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
            rest = "/".join(parts[2:] if spec.startswith("@") else parts[1:])
            if name in self.npm:
                d = self.npm[name]
                base = posixpath.join(d, rest) if rest else d
                hit = self._first([base, *(base + e for e in _TS_EXT), *(posixpath.join(base, "index" + e) for e in _TS_EXT),
                                   *(posixpath.join(d, "src", "index" + e) for e in _TS_EXT)])
                return hit or self._dir_hit(base), None
            if spec.startswith("node:"):
                return None, None
            return None, name
        if lang == "python":
            if spec.startswith("."):
                dots = len(spec) - len(spec.lstrip("."))
                d = here
                for _ in range(dots - 1):
                    d = posixpath.dirname(d)
                rel = spec.lstrip(".").replace(".", "/")
                base = posixpath.join(d, rel) if rel else d
                return self._first([base + ".py", posixpath.join(base, "__init__.py")]), None
            rel = spec.replace(".", "/")
            for prefix in ("", "src/", "lib/"):
                hit = self._first([prefix + rel + ".py", posixpath.join(prefix + rel, "__init__.py")])
                if hit:
                    return hit, None
            parts = rel.split("/")
            for k in range(len(parts) - 1, 0, -1):
                for prefix in ("", "src/"):
                    hit = self._first([prefix + "/".join(parts[:k]) + ".py", posixpath.join(prefix + "/".join(parts[:k]), "__init__.py")])
                    if hit:
                        return hit, None
            top = spec.split(".")[0]
            return None, top if top and not _py_stdlib(top) else None
        if lang == "go":
            for mod, d in sorted(self.gomod.items(), key=lambda kv: -len(kv[0])):
                if spec == mod or spec.startswith(mod + "/"):
                    target = posixpath.join(d, spec[len(mod) :].lstrip("/")).strip("/")
                    if target:
                        return self._dir_hit(target), None
                    return next((f for f in sorted(self.files) if "/" not in f and f.endswith(".go")), None), None
            first = spec.split("/")[0]
            return None, spec if "." in first else None
        if lang == "java":
            target = spec[:-2] if spec.endswith(".*") else spec
            parts = target.split(".")
            for k in range(len(parts), 0, -1):
                hit = self.java.get(".".join(parts[:k]))
                if hit:
                    return hit, None
            if spec.endswith(".*"):
                for fq, f in self.java.items():
                    if fq.startswith(target + "."):
                        return f, None
            return None, None if target.startswith(("java.", "javax.", "kotlin.")) else ".".join(parts[:3])
        if lang == "c":
            hit = self._first([posixpath.join(here, spec), spec, posixpath.join("include", spec)])
            if hit:
                return hit, None
            cands = [f for f in self.by_suffix.get(PurePosixPath(spec).name, []) if f.endswith(spec)]
            return (cands[0] if len(cands) == 1 else None), None
        return None, None


_PY_STD = {
    "abc", "argparse", "asyncio", "base64", "collections", "contextlib", "copy", "csv", "dataclasses", "datetime",
    "decimal", "enum", "functools", "glob", "hashlib", "heapq", "hmac", "http", "importlib", "io", "itertools", "json",
    "logging", "math", "operator", "os", "pathlib", "pickle", "platform", "queue", "random", "re", "secrets", "shutil",
    "signal", "socket", "sqlite3", "statistics", "string", "struct", "subprocess", "sys", "tempfile", "textwrap",
    "threading", "time", "traceback", "types", "typing", "unittest", "urllib", "uuid", "warnings", "weakref", "zipfile",
    "__future__",
}


def _py_stdlib(name: str) -> bool:
    return name in _PY_STD


# ---------------------------------------------------------------------------- rules


def load_rules(root: Path) -> dict[str, Any]:
    for rel in RULE_FILES:
        p = root / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
            if rel.endswith(".json"):
                data = json.loads(text)
            else:
                try:
                    import tomllib  # type: ignore[import-not-found]
                except ModuleNotFoundError:  # Python 3.10
                    import tomli as tomllib  # type: ignore[import-not-found,no-redef]
                data = tomllib.loads(text)
        except Exception as e:  # noqa: BLE001 - a broken rules file must not break the analysis
            return {"source": rel, "error": f"cannot read {rel}: {e}", "layers": [], "deny": []}
        layers = [str(x) for x in data.get("layers") or []]
        deny = []
        for d in data.get("deny") or []:
            to = d.get("to") or []
            deny.append({"from": str(d.get("from", "")), "to": [str(t) for t in (to if isinstance(to, list) else [to])], "reason": str(d.get("reason", ""))})
        return {"source": rel, "layers": layers, "deny": deny}
    return {"source": None, "layers": [], "deny": []}


def _matches(pattern: str, path: str) -> bool:
    pattern = pattern.strip().rstrip("/")
    if not pattern:
        return False
    if pattern in ("*", "**"):
        return True
    return path == pattern or path.startswith(pattern + "/") or fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, pattern + "/*")


def _layer_of(layers: list[str], path: str) -> int | None:
    for i, pat in enumerate(layers):
        if _matches(pat, path):
            return i
    return None


def check_rules(rules: dict[str, Any], dep: dict[str, Any]) -> list[dict[str, Any]]:
    src = dep["from_dir"]
    dst = dep["to_dir"]
    out = []
    for d in rules.get("deny") or []:
        if _matches(d["from"], src) and any(_matches(t, dst) for t in d["to"]):
            out.append({"rule": "deny", "detail": f"{d['from']} -> {', '.join(d['to'])}", "reason": d.get("reason", "")})
    layers = rules.get("layers") or []
    if layers and not dep.get("external"):
        a, b = _layer_of(layers, src), _layer_of(layers, dst)
        if a is not None and b is not None and b < a:
            out.append({"rule": "layer", "detail": f"{layers[a]} (layer {a + 1}) -> {layers[b]} (layer {b + 1})",
                        "reason": "a lower layer must not depend on a higher one"})
    return out


# ---------------------------------------------------------------------------- analysis


def _cycles_through(neighbors: Callable[[str], Iterable[str]], added: set[tuple[str, str]], limit: int = 10) -> list[list[str]]:
    adj: dict[str, set[str]] = {}

    def nb(k: str) -> set[str]:
        if k not in adj:
            adj[k] = {n for n in neighbors(k) if n != k}
        return adj[k]

    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for a, b in sorted(added):
        if a == b:
            continue
        # path b -> ... -> a closes a cycle with the added edge a -> b
        prev: dict[str, str | None] = {b: None}
        q = [b]
        while q:
            cur = q.pop(0)
            if cur == a:
                break
            for n in sorted(nb(cur)):
                if n not in prev:
                    prev[n] = cur
                    q.append(n)
        if a not in prev:
            continue
        path = [a]
        cur: str | None = a
        while cur is not None and cur != b:
            cur = prev[cur]
            if cur is not None:
                path.append(cur)
        cycle = [a, *reversed(path[1:]), a] if len(path) > 1 else [a, b, a]
        key = tuple(sorted(set(cycle)))
        if key in seen:
            continue
        seen.add(key)
        out.append(cycle)
        if len(out) >= limit:
            break
    return out


def analyze_architecture(
    *,
    repo_root: Path,
    head_root: Path,
    files: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    read_base: Callable[[str], str | None],
    read_head: Callable[[str], str | None],
    repo_files: Iterable[str],
) -> dict[str, Any]:
    grouper = Grouper(head_root, repo_root.name)
    all_files = set(repo_files) | {f["path"] for f in files if f["status"] != "D"}
    resolver = Resolver(head_root, all_files)

    def module_of(rel: str) -> str:
        return grouper.module_dir(rel)

    def pkg_of(rel: str) -> str:
        m = module_of(rel)
        inner = rel[len(m) + 1 :] if m else rel
        d = posixpath.dirname(inner)
        return d

    def label(m: str) -> str:
        return m or "."

    deps_added: list[dict[str, Any]] = []
    deps_removed: list[dict[str, Any]] = []
    head_deps: set[tuple[str, str]] = set()
    for f in files:
        lang = _LANG.get(PurePosixPath(f["path"]).suffix.lower())
        if lang is None:
            continue
        new_path = f["path"] if f["status"] != "D" else None
        old_path = f.get("old_path") or (f["path"] if f["status"] != "A" else None)
        head_text = read_head(new_path) if new_path else None
        base_text = read_base(old_path) if old_path else None
        head_imps = extract_imports(lang, head_text or "")
        base_imps = extract_imports(lang, base_text or "")

        def resolved(imps: list[Import], src: str) -> dict[str, tuple[Import, str | None, str | None]]:
            out: dict[str, tuple[Import, str | None, str | None]] = {}
            for imp in imps:
                target, ext = resolver.resolve(lang, src, imp.spec)
                key = target or (f"external:{ext}" if ext else None)
                if key and key not in out:
                    out[key] = (imp, target, ext)
            return out

        head_r = resolved(head_imps, new_path) if new_path else {}
        base_r = resolved(base_imps, old_path) if old_path else {}
        src_mod = module_of(new_path or old_path or f["path"])
        for _imp, target, _ext in head_r.values():
            if target:
                head_deps.add((src_mod, module_of(target)))

        def entry(src: str, imp: Import, target: str | None, ext: str | None) -> dict[str, Any] | None:
            fm, fp = module_of(src), pkg_of(src)
            if target:
                tm, tp = module_of(target), pkg_of(target)
                if (fm, fp) == (tm, tp):
                    return None
                to_dir = posixpath.dirname(target)
            else:
                tm, tp, to_dir = f"external:{ext}", "", f"external:{ext}"
            return {
                "from_module": label(fm), "from_package": fp, "from_dir": posixpath.dirname(src),
                "to_module": label(tm), "to_package": tp, "to_dir": to_dir, "to_file": target,
                "external": target is None, "cross_module": fm != tm,
                "file": src, "line": imp.line, "import": imp.spec, "code": imp.text[:200],
            }

        for key in head_r.keys() - base_r.keys():
            e = entry(new_path or f["path"], *head_r[key])
            if e:
                deps_added.append(e)
        for key in base_r.keys() - head_r.keys():
            e = entry(old_path or f["path"], *base_r[key])
            if e:
                deps_removed.append(e)

    by_id = {n["id"]: n for n in nodes}
    for e in edges:
        s, t = by_id.get(e["source"]), by_id.get(e["target"])
        if s and t:
            head_deps.add((module_of(t["path"]), module_of(s["path"])))

    contracts = []
    for n in nodes:
        if n.get("change") not in CONTRACT:
            continue
        own = module_of(n["path"])
        callers = [by_id[e["target"]] for e in edges if e["source"] == n["id"] and e["target"] in by_id and not by_id[e["target"]]["change"]]
        others = sorted({label(module_of(c["path"])) for c in callers} - {label(own)})
        contracts.append({
            "id": n["id"], "qualname": n["qualname"], "change": n["change"], "file": n["path"], "line": n["lines"][0],
            "module": label(own), "callers": len(callers), "external_modules": others,
            "compile_errors": sum(1 for c in callers if c.get("breaking")),
        })
    contracts.sort(key=lambda c: (-len(c["external_modules"]), -c["callers"]))

    def raw(m: str) -> str:
        return "" if m == "." else m

    added_mod = {(raw(d["from_module"]), raw(d["to_module"])) for d in deps_added if not d["external"] and d["cross_module"]}
    known: dict[str, set[str]] = {}
    for a, b in head_deps | added_mod:
        known.setdefault(a, set()).add(b)
    changed_src = {f["path"] for f in files}
    by_module: dict[str, list[str]] | None = None
    budget = [SCAN_BUDGET]

    def neighbors(m: str) -> set[str]:
        nonlocal by_module
        out = set(known.get(m, ()))
        if by_module is None:
            by_module = {}
            for rel in all_files:
                if rel in changed_src:
                    continue
                lang = _LANG.get(PurePosixPath(rel).suffix.lower())
                if lang:
                    by_module.setdefault(module_of(rel), []).append(rel)
        for rel in by_module.get(m, []):
            if budget[0] <= 0:
                break
            budget[0] -= 1
            lang = _LANG[PurePosixPath(rel).suffix.lower()]
            for imp in extract_imports(lang, read_head(rel) or ""):
                target, _ = resolver.resolve(lang, rel, imp.spec)
                if target:
                    out.add(module_of(target))
        return out

    cycles = [[label(m) for m in c] for c in _cycles_through(neighbors, added_mod)]

    rules = load_rules(head_root)
    violations = []
    for d in deps_added:
        for v in check_rules(rules, d):
            violations.append(v | {"from": d["from_dir"] or ".", "to": d["to_dir"] or ".", "file": d["file"], "line": d["line"],
                                   "import": d["import"], "code": d["code"]})

    modules = sorted({label(module_of(rel)) for rel in all_files})
    return {
        "rules": rules,
        "modules": modules[:MAX_MODULES],
        "deps_added": deps_added,
        "deps_removed": deps_removed,
        "contracts": contracts,
        "cycles": cycles,
        "violations": violations,
        "summary": {
            "deps_added": len(deps_added),
            "deps_removed": len(deps_removed),
            "cross_module_added": sum(1 for d in deps_added if d["cross_module"] and not d["external"]),
            "external_added": sum(1 for d in deps_added if d["external"]),
            "contracts_crossing": sum(1 for c in contracts if c["external_modules"]),
            "cycles": len(cycles),
            "violations": len(violations),
            "has_rules": rules["source"] is not None,
            "cycle_scan_truncated": budget[0] <= 0,
            "modules": len(modules),
        },
    }
