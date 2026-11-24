"""MCP server (stdio). Keeps language servers warm and re-analyzes when the tree changes.

Tools: analyze_diff, get_impact, explain, get_review_context, get_architecture, submit_review, open_review_ui.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any

from .analysis.impact import Options
from .analysis.pipeline import analyze
from .analysis.report import review_context
from .git import diff as gitdiff
from .lsp.pool import ServerPool
from .lsp.registry import Registry, cache_dir

log = logging.getLogger("ripplelens.mcp")


def dirty_marker(repo: Path) -> Path:
    tag = hashlib.sha1(str(repo).lower().encode()).hexdigest()[:16]
    return cache_dir() / "dirty" / tag


def fingerprint(repo: Path) -> str:
    """Cheap identity of the working tree state: HEAD + status + mtimes of changed files."""
    h = hashlib.sha1()
    h.update((gitdiff.rev_parse(repo, "HEAD") or "").encode())
    status = gitdiff.git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=normal", check=False)
    h.update(status.encode())
    for entry in status.split("\0"):
        rel = entry[3:] if len(entry) > 3 else ""
        if not rel:
            continue
        try:
            st = (repo / rel).stat()
            h.update(f"{rel}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            h.update(f"{rel}:gone".encode())
    return h.hexdigest()


class Session:
    def __init__(self, default_repo: str | None):
        self.default_repo = default_repo
        self.pools: dict[str, ServerPool] = {}
        self.reports: dict[str, dict[str, Any]] = {}
        self.meta: dict[str, dict[str, Any]] = {}
        self.ui = None
        self.lock = threading.RLock()

    def repo(self, repo: str | None) -> Path:
        candidate = repo or self.default_repo or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        return gitdiff.repo_root(candidate)

    def pool(self, root: Path) -> ServerPool:
        key = str(root)
        if key not in self.pools:
            self.pools[key] = ServerPool(Registry.load(root))
        return self.pools[key]

    def report(
        self, repo: str | None, base: str | None = None, head: str | None = None, depth: int | None = None, force: bool = False
    ) -> tuple[dict[str, Any], bool]:
        """Return (report, reanalyzed)."""
        with self.lock:
            root = self.repo(repo)
            key = str(root)
            meta = self.meta.get(key, {})
            args = {
                "base": base if base is not None else meta.get("args", {}).get("base"),
                "head": head if head is not None else meta.get("args", {}).get("head"),
                "depth": depth if depth is not None else meta.get("args", {}).get("depth", 3),
            }
            fp = fingerprint(root)
            marker = dirty_marker(root)
            marker_time = marker.stat().st_mtime if marker.exists() else 0.0
            fresh = (
                key in self.reports
                and not force
                and meta.get("fingerprint") == fp
                and meta.get("args") == args
                and marker_time <= meta.get("time", 0)
            )
            if fresh:
                return self.reports[key], False
            opt = Options(max_depth=int(args["depth"]))
            rep = analyze(root, args["base"], args["head"], opt, pool=self.pool(root), progress=lambda m: log.info(m))
            self.reports[key] = rep
            self.meta[key] = {"fingerprint": fp, "args": args, "time": time.time()}
            if self.ui is not None:
                self.ui.update(rep)
            return rep, True

    def shutdown(self) -> None:
        for p in self.pools.values():
            p.shutdown()
        if self.ui is not None:
            self.ui.stop()


# ----------------------------------------------------------------------------- helpers


def _line(rep: dict[str, Any], rel: str, line: int) -> str:
    try:
        lines = (Path(rep["repo"]["analyzed_root"]) / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[line - 1].strip()[:200] if 0 < line <= len(lines) else ""
    except OSError:
        return ""


def _brief(n: dict[str, Any]) -> dict[str, Any]:
    out = {"id": n["id"], "qualname": n["qualname"], "kind": n["kind"], "at": f"{n['path']}:{n['lines'][0]}"}
    if n["change"]:
        out["change"] = n["change"]
        sig = n.get("signature") or {}
        if sig.get("before") and sig.get("after") and sig["before"] != sig["after"]:
            out["signature"] = {"before": sig["before"], "after": sig["after"]}
        if n.get("renamed_from"):
            out["renamed_from"] = n["renamed_from"]
        out["impacted"] = n.get("impacted", 0)
    else:
        out.update(via=n["via"], depth=n["depth"], score=n["score"])
    if n["confidence"] != "precise":
        out["confidence"] = n["confidence"]
        out["reasons"] = n["reasons"]
    if n["breaking"]:
        out["compile_errors"] = [f"{n['path']}:{b['line']}: {b['message']}" for b in n["breaking"]]
    if n.get("truncated"):
        out["truncated"] = n["truncated"]
    return out


def _resolve(rep: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    nodes = rep["nodes"]
    for pred in (
        lambda n: n["id"] == symbol,
        lambda n: n["qualname"] == symbol,
        lambda n: n["name"] == symbol,
        lambda n: f"{n['path']}:{n['lines'][0]}" == symbol,
        lambda n: symbol.lower() in n["qualname"].lower(),
    ):
        hits = [n for n in nodes if pred(n)]
        if hits:
            return hits
    return []


def summarize(rep: dict[str, Any], top: int = 30) -> dict[str, Any]:
    nodes = rep["nodes"]
    impacted = [n for n in nodes if not n["change"]]
    by_id = {n["id"]: n for n in nodes}
    return {
        "range": f"{rep['repo']['base']['ref']} ({rep['repo']['base']['sha'][:10]}) .. {rep['repo']['head']['ref']}",
        "summary": rep["summary"],
        "servers": [
            {k: s.get(k) for k in ("name", "status", "reason", "version")} | ({"notes": s["project_notes"]} if s.get("project_notes") else {})
            for s in rep["servers"]
        ],
        "changed": [_brief(n) for n in nodes if n["change"]],
        "compile_errors": [
            {"in": n["qualname"], "at": f"{n['path']}:{b['line']}", "message": b["message"]}
            for n in nodes
            for b in n["breaking"]
        ],
        "top_impacted": [
            _brief(n) | {"why": [by_id[p]["qualname"] for p in rep["paths"].get(n["id"], []) if p in by_id]}
            for n in impacted[:top]
        ],
        "omitted_impacted": max(0, len(impacted) - top) if rep["summary"].get("impact_analyzed", True) else None,
        "warnings": rep.get("warnings", [])[:8],
        "truncation": rep["truncation"][:10],
        "blind_spots": rep["blind_spots"],
        "files_degraded": {f["path"]: f["degraded"] for f in rep["files"] if f["degraded"]},
        "architecture": architecture_brief(rep.get("architecture")),
        "next": "get_impact(symbol) for call sites and code; get_architecture() for module dependencies and rules; "
        "explain(from, to) for a path; submit_review(...) to hand your pre-review to the human; "
        "open_review_ui() for the human view.",
    }


def architecture_brief(arch: dict[str, Any] | None, top: int = 10) -> dict[str, Any] | None:
    if not arch:
        return None
    cross = [d for d in arch["deps_added"] if d["cross_module"] or d["external"]]
    return {
        "summary": arch["summary"],
        "rules": arch["rules"].get("source"),
        "violations": [f"{v['file']}:{v['line']} {v['rule']}: {v['from']} -> {v['to']} ({v['reason'] or v['detail']})" for v in arch["violations"][:top]],
        "new_cycles": [" -> ".join(c) for c in arch["cycles"][:top]],
        "deps_added": [f"{d['from_module']} -> {d['to_module']} at {d['file']}:{d['line']} ({d['import']})" for d in cross[:top]],
        "contracts_crossing_modules": [
            f"{c['qualname']} ({c['change']}) in {c['module']} -> {', '.join(c['external_modules'])}"
            for c in arch["contracts"]
            if c["external_modules"]
        ][:top],
    }


def architecture_full(rep: dict[str, Any]) -> dict[str, Any]:
    arch = rep.get("architecture")
    if not arch:
        return {"error": "no architecture evidence in this report (analysis failed or an old report)", "warnings": rep["warnings"][:5]}
    out = dict(arch)
    if "modules" not in out:
        out["modules"] = sorted({g["module_path"] or "." for g in rep.get("groups") or []})
    src = arch["rules"].get("source")
    if src:
        try:
            out["rules_text"] = (Path(rep["repo"]["analyzed_root"]) / src).read_text(encoding="utf-8")[:6000]
        except OSError:
            pass
    else:
        out["rules_hint"] = (
            "No .ripplelens/architecture.toml: judge dependencies on their merits and phrase findings as suggestions, "
            "not violations. A rules file looks like: layers = [\"apps/*\", \"packages/api\", \"packages/core\"] and "
            "[[deny]] from = \"packages/core\" to = [\"packages/api\"] reason = \"...\"."
        )
    return out


def impact_of(rep: dict[str, Any], symbol: str, depth: int = 2) -> dict[str, Any]:
    hits = _resolve(rep, symbol)
    if not hits:
        return {"error": f"no changed or impacted entity matches {symbol!r}", "hint": "use an id from analyze_diff"}
    if len(hits) > 1 and not any(h["id"] == symbol for h in hits):
        return {"ambiguous": [_brief(h) for h in hits[:20]]}
    n = hits[0]
    by_id = {x["id"]: x for x in rep["nodes"]}
    out_edges: dict[str, list[dict[str, Any]]] = {}
    in_edges: dict[str, list[dict[str, Any]]] = {}
    for e in rep["edges"]:
        out_edges.setdefault(e["source"], []).append(e)
        in_edges.setdefault(e["target"], []).append(e)

    def sites(e: dict[str, Any]) -> list[dict[str, Any]]:
        t = by_id.get(e["target"])
        if not t:
            return []
        return [{"at": f"{t['path']}:{s}", "code": _line(rep, t["path"], s)} for s in e["sites"][:5]]

    downstream = []
    frontier = deque([(n["id"], 0)])
    seen = {n["id"]}
    while frontier:
        cur, d = frontier.popleft()
        if d >= depth:
            continue
        for e in out_edges.get(cur, []):
            t = by_id.get(e["target"])
            if not t:
                continue
            downstream.append({"from": by_id[cur]["qualname"], "kind": e["kind"], "to": _brief(t), "sites": sites(e)})
            if t["id"] not in seen:
                seen.add(t["id"])
                frontier.append((t["id"], d + 1))
    upstream = [
        {"from": _brief(by_id[e["source"]]), "kind": e["kind"], "sites": sites(e)}
        for e in in_edges.get(n["id"], [])
        if e["source"] in by_id
    ]
    return {
        "entity": _brief(n),
        "why": [by_id[p]["qualname"] for p in rep["paths"].get(n["id"], []) if p in by_id],
        "upstream": upstream,
        "downstream": downstream,
        "downstream_depth": depth,
    }


def explain_path(rep: dict[str, Any], src: str, dst: str) -> dict[str, Any]:
    a, b = _resolve(rep, src), _resolve(rep, dst)
    if not a or not b:
        return {"error": f"unknown entity: {src if not a else dst!r}"}
    by_id = {x["id"]: x for x in rep["nodes"]}
    adj: dict[str, list[dict[str, Any]]] = {}
    for e in rep["edges"]:
        adj.setdefault(e["source"], []).append(e)
    for start in a:
        targets = {x["id"] for x in b}
        prev: dict[str, dict[str, Any] | None] = {start["id"]: None}
        q = deque([start["id"]])
        while q:
            cur = q.popleft()
            if cur in targets:
                steps = []
                while prev[cur] is not None:
                    e = prev[cur]
                    assert e is not None
                    t = by_id[e["target"]]
                    steps.append(
                        {
                            "from": by_id[e["source"]]["qualname"],
                            "kind": e["kind"],
                            "to": t["qualname"],
                            "sites": [{"at": f"{t['path']}:{s}", "code": _line(rep, t["path"], s)} for s in e["sites"][:3]],
                        }
                    )
                    cur = e["source"]
                return {"path": list(reversed(steps)), "hops": len(steps)}
            for e in adj.get(cur, []):
                if e["target"] not in prev:
                    prev[e["target"]] = e
                    q.append(e["target"])
    return {"path": None, "note": f"{src} does not reach {dst} in the computed blast radius (edges point from a change to what it impacts)"}


# ----------------------------------------------------------------------------- server


def build_server(session: Session):
    import anyio

    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer

    mcp = MCPServer(
        "ripplelens",
        instructions=(
            "ripplelens computes the function-level blast radius of the current git diff using compiler-backed "
            "language servers. Call analyze_diff first; it returns changed entities, compile errors at impacted call "
            "sites, and the top impacted callers with why-paths. Results are re-computed automatically when files change."
        ),
    )

    @mcp.tool()
    async def analyze_diff(base: str | None = None, head: str | None = None, depth: int = 3, repo: str | None = None) -> dict:
        """Analyze the diff and return changed entities, compile errors and top impacted callers.

        base: compare against merge-base(base, head); default is HEAD (working-tree changes) or HEAD~1 when clean.
        head: a commit to analyze instead of the working tree.
        depth: max caller depth for signature/removed changes (body changes use min(2, depth)).
        """
        rep, fresh = await anyio.to_thread.run_sync(lambda: session.report(repo, base, head, depth, force=False))
        out = summarize(rep)
        out["reanalyzed"] = fresh
        return out

    @mcp.tool()
    async def get_impact(symbol: str, depth: int = 2, repo: str | None = None) -> dict:
        """Details for one entity: why it is impacted, who it impacts (with call-site code), confidence.

        symbol: node id from analyze_diff (best), qualified name, name, or path:line.
        """
        rep, _ = await anyio.to_thread.run_sync(lambda: session.report(repo))
        return impact_of(rep, symbol, depth)

    @mcp.tool()
    async def explain(from_symbol: str, to_symbol: str, repo: str | None = None) -> dict:
        """Shortest impact path from a changed (or impacted) entity to another, with edge kinds and call sites."""
        rep, _ = await anyio.to_thread.run_sync(lambda: session.report(repo))
        return explain_path(rep, from_symbol, to_symbol)

    @mcp.tool()
    async def get_review_context(budget_tokens: int = 4000, repo: str | None = None) -> str:
        """Prioritized plain-text review context (compile errors, contract changes, top impacted) within a token budget."""
        rep, _ = await anyio.to_thread.run_sync(lambda: session.report(repo))
        return review_context(rep, budget_tokens)

    @mcp.tool()
    async def get_architecture(repo: str | None = None) -> dict:
        """Module/package evidence for an architecture review: dependencies the diff adds or removes (with file:line),
        contract changes whose callers live in other modules, new module cycles, and violations of
        .ripplelens/architecture.toml (rules text included when present)."""
        rep, _ = await anyio.to_thread.run_sync(lambda: session.report(repo))
        return architecture_full(rep)

    @mcp.tool()
    async def submit_review(summary: str, verdict: str, findings: list[dict], repo: str | None = None) -> dict:
        """Hand your pre-review to the human reviewer; it appears in the review UI where each finding is accepted or rejected.

        summary: 2-5 sentences: what the change does architecturally and the main risks.
        verdict: approve | comment | request_changes.
        findings: list of {severity: high|medium|low, kind: architecture|contract|behavior, title, file, line,
          scope (module or package directory the finding is about; defaults to the file's directory),
          evidence (quote the tool output that proves it), suggestion}. Use file:line values from the tools.
        Replaces any earlier pre-review of this repository.
        """
        rep, _ = await anyio.to_thread.run_sync(lambda: session.report(repo))
        from . import review_store

        data = review_store.submit(rep["repo"]["root"], rep, summary, verdict, findings)
        state = review_store.with_state(data, rep) or {}
        return {"stored": str(review_store.store_path(rep["repo"]["root"])), "findings": state.get("counts"),
                "next": "call open_review_ui() and tell the user to confirm or reject each finding there"}

    @mcp.tool()
    async def open_review_ui(repo: str | None = None) -> dict:
        """Open the interactive review UI in the user's browser and return its URL."""
        rep, _ = await anyio.to_thread.run_sync(lambda: session.report(repo))
        from .http_server import ReviewServer

        with session.lock:
            if session.ui is None:
                session.ui = ReviewServer(rep, 0)
                session.ui.start()
            else:
                session.ui.update(rep)
            url = session.ui.url
        opened = False
        if not os.environ.get("RIPPLELENS_NO_BROWSER"):
            opened = webbrowser.open(url)
        return {"url": url, "opened_browser": opened}

    return mcp


def main(repo: str | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="ripplelens: %(message)s")
    session = Session(repo if repo and repo != "." else None)
    server = build_server(session)
    try:
        server.run("stdio")
    finally:
        session.shutdown()
    return 0
