"""LSP capability spike: run each language server on tests/fixtures/<lang>
and check documentSymbol / hover / callHierarchy / references / implementation
against hand-written expectations.

  python eval/spike.py [lang ...] [--out DIR]

Exit code is 0 even when expectations fail; the JSON report is the artefact.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ripplelens.analysis.symbols import SymbolIndex, flatten
from ripplelens.lsp.client import LspError, norm_path, uri_to_path
from ripplelens.lsp.pool import ServerPool, ServerUnavailable
from ripplelens.lsp.registry import Registry

FIXTURES = ROOT / "tests" / "fixtures"

# role -> (file, qualname suffix)
CASES: dict[str, dict[str, Any]] = {
    "go": {
        "server": "gopls",
        "files": ["lib/lib.go", "app/app.go"],
        "symbols": {
            "helper": ("lib/lib.go", "Helper"),
            "circle_area": ("lib/lib.go", "Circle.Area"),
            "shape_area": ("lib/lib.go", "Shape.Area"),
            "limit": ("lib/lib.go", "Limit"),
        },
    },
    "ts": {
        "server": "typescript",
        "files": ["src/lib.ts", "src/app.ts"],
        "symbols": {
            "helper": ("src/lib.ts", "helper"),
            "circle_area": ("src/lib.ts", "Circle.area"),
            "shape_area": ("src/lib.ts", "Shape.area"),
            "limit": ("src/lib.ts", "LIMIT"),
        },
    },
    "python": {
        "server": "pyright",
        "files": ["fx/lib.py", "fx/app.py"],
        "symbols": {
            "helper": ("fx/lib.py", "helper"),
            "circle_area": ("fx/lib.py", "Circle.area"),
            "shape_area": ("fx/lib.py", "Shape.area"),
            "limit": ("fx/lib.py", "LIMIT"),
        },
    },
    "java": {
        "server": "jdtls",
        "files": [
            "src/main/java/fx/lib/Lib.java",
            "src/main/java/fx/lib/Circle.java",
            "src/main/java/fx/lib/Shape.java",
            "src/main/java/fx/app/App.java",
        ],
        "symbols": {
            "helper": ("src/main/java/fx/lib/Lib.java", "helper"),
            "circle_area": ("src/main/java/fx/lib/Circle.java", "Circle.area"),
            "shape_area": ("src/main/java/fx/lib/Shape.java", "Shape.area"),
            "limit": ("src/main/java/fx/lib/Lib.java", "LIMIT"),
        },
    },
    "c": {
        "server": "clangd",
        "files": ["lib.c", "app.c", "lib.h"],
        "compile": ["lib.c", "app.c"],
        "compiler": "clang",
        "symbols": {
            "helper": ("lib.c", "helper"),
            "circle_area": ("lib.c", "circle_area"),
            "limit": ("lib.c", "limit_value"),
        },
    },
    "cpp": {
        "server": "clangd",
        "files": ["lib.cpp", "app.cpp", "lib.hpp"],
        "compile": ["lib.cpp", "app.cpp"],
        "compiler": "clang++",
        "symbols": {
            "helper": ("lib.cpp", "helper"),
            "circle_area": ("lib.cpp", "area"),
            "shape_area": ("lib.hpp", "Shape.area"),
            "limit": ("lib.hpp", "kLimit"),
        },
    },
}

# expected enclosing-function names (lower-cased last component)
EXPECT = {
    "helper.calls": {"a", "b"},
    "helper.refs_extra": {"c"},  # passed as a value: only references can see it
    "circle_area.calls_direct": {"d"},
    "circle_area.calls_via_interface": {"total"},
    "shape_area.calls": {"total"},
    "shape_area.impls": {"area"},
    "limit.refs": {"helper"},
}


def short(name: str) -> str:
    n = name.split("(")[0].strip()
    for sep in ("::", "."):
        n = n.split(sep)[-1]
    return n.lower()


def prepare_workspace(lang: str, case: dict[str, Any], base: Path) -> Path:
    src = FIXTURES / lang
    dst = base / lang
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    dst = dst.resolve()
    if "compile" in case:
        entries = []
        std = "-std=c11" if case["compiler"] == "clang" else "-std=c++17"
        for f in case["compile"]:
            entries.append(
                {"directory": str(dst), "file": str(dst / f), "arguments": [case["compiler"], std, "-c", f]}
            )
        (dst / "compile_commands.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return dst


class Spike:
    def __init__(self, handle, ws: Path):
        self.h = handle
        self.ws = ws
        self.indexes: dict[str, SymbolIndex] = {}
        self.timings: dict[str, float] = {}

    def timed(self, label: str, fn, *a, **kw):
        t0 = time.monotonic()
        try:
            return fn(*a, **kw)
        finally:
            self.timings[label] = round(time.monotonic() - t0, 3)

    def index(self, path: str) -> SymbolIndex:
        key = norm_path(path)
        if key not in self.indexes:
            self.h.ensure_open(path)
            syms = self.h.client.document_symbols(path, timeout=120)
            self.indexes[key] = SymbolIndex(flatten(syms, key))
        return self.indexes[key]

    def find(self, rel: str, suffix: str):
        path = str(self.ws / rel)
        idx = self.index(path)
        want = suffix.replace("::", ".")
        cands = [e for e in idx.entities if e.qualname.replace("::", ".").endswith(want) or e.name == want]
        cands.sort(key=lambda e: (not e.qualname.replace("::", ".").endswith(want), e.depth))
        return path, (cands[0] if cands else None)

    def enclosing(self, uri: str, line: int) -> str:
        path = uri_to_path(uri)
        try:
            ent = self.index(path).enclosing_for_impact(line)
        except (LspError, OSError):
            return "?"
        return ent.qualname if ent else "<module>"


def run_lang(lang: str, pool: ServerPool, base: Path) -> dict[str, Any]:
    case = CASES[lang]
    report: dict[str, Any] = {"lang": lang, "server": case["server"], "platform": platform.platform()}
    spec = pool.registry.servers[case["server"]]
    ws = prepare_workspace(lang, case, base)
    t0 = time.monotonic()
    try:
        h = pool.get(spec, ws)
    except ServerUnavailable as e:
        report["error"] = e.reason
        return report
    report["server_info"] = h.client.server_info
    report["init_seconds"] = round(h.init_seconds, 2)
    files = [str(ws / f) for f in case["files"]]
    h.warm_up(files)
    report["ready"] = h.ready
    report["ready_reason"] = h.ready_reason
    report["ready_seconds"] = round(time.monotonic() - t0, 2)
    seen, active = h.client.progress_snapshot()
    report["progress_seen"] = seen
    caps = h.client.capabilities
    report["capabilities"] = {
        k: bool(caps.get(k))
        for k in (
            "documentSymbolProvider",
            "hoverProvider",
            "referencesProvider",
            "implementationProvider",
            "callHierarchyProvider",
            "typeHierarchyProvider",
        )
    }
    sp = Spike(h, ws)
    results: dict[str, Any] = {}
    checks: dict[str, Any] = {}

    for role, (rel, suffix) in case["symbols"].items():
        r: dict[str, Any] = {}
        try:
            path, ent = sp.find(rel, suffix)
        except LspError as e:
            r["error"] = f"documentSymbol: {e}"
            results[role] = r
            continue
        if not ent:
            r["error"] = f"symbol {suffix} not found in {rel}"
            r["symbols"] = [e.qualname for e in sp.index(str(ws / rel)).entities]
            results[role] = r
            continue
        r["entity"] = {"qualname": ent.qualname, "kind": ent.kind_name, "line": ent.sel_line + 1}
        line, ch = ent.sel_line, ent.sel_char
        try:
            hv = sp.timed(f"{role}.hover", h.client.hover, path, line, ch)
            r["hover"] = _hover_text(hv)[:300]
        except LspError as e:
            r["hover_error"] = str(e)
        try:
            refs = sp.timed(f"{role}.references", h.client.references, path, line, ch)
            r["references"] = sorted({sp.enclosing(x["uri"], x["range"]["start"]["line"]) for x in refs})
        except LspError as e:
            r["references_error"] = str(e)
        if ent.is_callable:
            try:
                items = sp.timed(f"{role}.prepareCallHierarchy", h.client.prepare_call_hierarchy, path, line, ch)
                callers: set[str] = set()
                for it in items[:1]:
                    inc = sp.timed(f"{role}.incomingCalls", h.client.incoming_calls, it)
                    for c in inc:
                        fr = c["from"]
                        callers.add(sp.enclosing(fr["uri"], fr["selectionRange"]["start"]["line"]))
                r["incoming"] = sorted(callers)
                r["prepare_items"] = len(items)
            except LspError as e:
                r["incoming_error"] = str(e)
            try:
                impls = sp.timed(f"{role}.implementation", h.client.implementation, path, line, ch)
                r["implementations"] = sorted({sp.enclosing(x["uri"], x["range"]["start"]["line"]) for x in impls})
            except LspError as e:
                r["implementation_error"] = str(e)
        results[role] = r

    def names(role: str, key: str) -> set[str]:
        return {short(x) for x in results.get(role, {}).get(key, [])}

    if "helper" in results:
        checks["helper: incomingCalls finds direct callers {a,b}"] = EXPECT["helper.calls"] <= names("helper", "incoming")
        checks["helper: references finds value use in c"] = EXPECT["helper.refs_extra"] <= names("helper", "references")
        checks["helper: incomingCalls also lists c"] = "c" in names("helper", "incoming")
    if "circle_area" in results:
        checks["Circle.area: incomingCalls finds d"] = EXPECT["circle_area.calls_direct"] <= names("circle_area", "incoming")
        checks["Circle.area: incomingCalls includes interface-dispatch caller total"] = (
            EXPECT["circle_area.calls_via_interface"] <= names("circle_area", "incoming")
        )
        checks["Circle.area: references include total"] = (
            EXPECT["circle_area.calls_via_interface"] <= names("circle_area", "references")
        )
    if "shape_area" in results:
        checks["Shape.area: incomingCalls finds total"] = EXPECT["shape_area.calls"] <= names("shape_area", "incoming")
        checks["Shape.area: implementation finds Circle.area"] = EXPECT["shape_area.impls"] <= names(
            "shape_area", "implementations"
        )
    if "limit" in results:
        checks["constant: references finds helper"] = EXPECT["limit.refs"] <= names("limit", "references")

    report["results"] = results
    report["checks"] = checks
    report["timings"] = sp.timings
    report["total_seconds"] = round(time.monotonic() - t0, 2)
    return report


def _hover_text(hv: Any) -> str:
    if not hv:
        return ""
    c = hv.get("contents")
    if isinstance(c, dict):
        return c.get("value", "")
    if isinstance(c, list):
        return "\n".join(x if isinstance(x, str) else x.get("value", "") for x in c)
    return str(c)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("langs", nargs="*", default=list(CASES))
    ap.add_argument("--out", default=str(ROOT / "eval" / "spike-results"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.gettempdir()) / "ripplelens-spike"
    base.mkdir(exist_ok=True)
    pool = ServerPool(Registry.load())
    plat = "windows" if sys.platform == "win32" else sys.platform
    summary = []
    try:
        for lang in args.langs:
            print(f"== {lang}", flush=True)
            rep = run_lang(lang, pool, base)
            (out / f"{plat}-{lang}.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
            if "error" in rep:
                print(f"   unavailable: {rep['error']}")
                summary.append((lang, "unavailable"))
                continue
            print(f"   ready={rep['ready']} in {rep['ready_seconds']}s ({rep.get('ready_reason') or 'ok'})")
            for name, ok in rep["checks"].items():
                print(f"   [{'x' if ok else ' '}] {name}")
            for role, r in rep["results"].items():
                if any(k.endswith("error") for k in r):
                    print(f"   ! {role}: {[v for k, v in r.items() if k.endswith('error')]}")
            passed = sum(rep["checks"].values())
            summary.append((lang, f"{passed}/{len(rep['checks'])}"))
    finally:
        pool.shutdown()
    print("summary:", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
