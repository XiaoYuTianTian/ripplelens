"""MCP server: pure helpers on a synthetic report, plus an end-to-end stdio session (lsp marker)."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest
from scenarios import SCENARIOS, materialize

from ripplelens.mcp_server import dirty_marker, explain_path, impact_of, summarize


def _node(nid, name, path, line, change=None, via=None, depth=0, score=1.0, breaking=()):
    return {
        "id": nid, "path": path, "name": name, "qualname": name, "kind": "function", "lines": [line, line + 2],
        "change": change, "depth": depth, "score": score, "via": via, "parent": None, "seeds": [],
        "confidence": "precise", "reasons": [], "breaking": list(breaking), "truncated": None,
        "signature": {"before": "f(x)", "after": "f(x, y)"} if change else None, "impacted": 2 if change else 0,
    }


def _report(root: Path) -> dict:
    (root / "lib.py").write_text("def f(x, y):\n    return x\n", encoding="utf-8")
    (root / "app.py").write_text("def a():\n    return f(1)\n\ndef e():\n    return a()\n", encoding="utf-8")
    nodes = [
        _node("lib.py#f@1", "f", "lib.py", 1, change="signature"),
        _node("app.py#a@1", "a", "app.py", 1, via="call", depth=1, score=0.6, breaking=[{"line": 2, "message": "missing y"}]),
        _node("app.py#e@4", "e", "app.py", 4, via="call", depth=2, score=0.36),
    ]
    return {
        "repo": {"analyzed_root": str(root), "base": {"ref": "HEAD", "sha": "0" * 40}, "head": {"ref": "working tree"}},
        "summary": {"entities_changed": 1, "impacted": 2, "impacted_files": 1, "breaking": 1},
        "servers": [{"name": "pyright", "status": "ok", "reason": None, "version": "1"}],
        "files": [{"path": "lib.py", "degraded": []}],
        "nodes": nodes,
        "edges": [
            {"source": "lib.py#f@1", "target": "app.py#a@1", "kind": "call", "sites": [2]},
            {"source": "app.py#a@1", "target": "app.py#e@4", "kind": "call", "sites": [5]},
        ],
        "paths": {"app.py#a@1": ["lib.py#f@1", "app.py#a@1"], "app.py#e@4": ["lib.py#f@1", "app.py#a@1", "app.py#e@4"]},
        "truncation": [],
        "blind_spots": {},
    }


def test_summarize_and_lookup(tmp_path):
    rep = _report(tmp_path)
    s = summarize(rep)
    assert s["changed"][0]["signature"] == {"before": "f(x)", "after": "f(x, y)"}
    assert s["compile_errors"] == [{"in": "a", "at": "app.py:2", "message": "missing y"}]
    assert s["top_impacted"][1]["why"] == ["f", "a", "e"]

    imp = impact_of(rep, "f", depth=2)
    assert [d["to"]["qualname"] for d in imp["downstream"]] == ["a", "e"]
    assert imp["downstream"][0]["sites"] == [{"at": "app.py:2", "code": "return f(1)"}]
    assert impact_of(rep, "a")["upstream"][0]["from"]["qualname"] == "f"
    assert "error" in impact_of(rep, "nope")

    path = explain_path(rep, "f", "e")
    assert [(p["from"], p["to"]) for p in path["path"]] == [("f", "a"), ("a", "e")]
    assert explain_path(rep, "e", "f")["path"] is None


def test_hook_post_edit_marks_dirty(tmp_path, monkeypatch):
    sc = next(s for s in SCENARIOS if s.name == "python-cosmetic")
    repo = materialize(sc, tmp_path / "repo")
    monkeypatch.setenv("RIPPLELENS_CACHE", str(tmp_path / "cache"))
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(repo / "lib.py")}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    from ripplelens.cli import main

    assert main(["hook", "post-edit"]) == 0
    assert dirty_marker(repo.resolve()).exists()
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert main(["hook", "post-edit"]) == 0


@pytest.mark.lsp
def test_mcp_stdio_session(tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from ripplelens.lsp.registry import Registry

    if Registry.load().servers["pyright"].build_command(Path(".")) is None:
        pytest.skip("pyright not installed")
    sc = next(s for s in SCENARIOS if s.name == "python-signature")
    repo = materialize(sc, tmp_path / "repo")
    env = dict(os.environ, RIPPLELENS_NO_BROWSER="1", PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "ripplelens", "mcp", "--repo", str(repo)], env=env)

    def payload(result):
        assert not getattr(result, "is_error", getattr(result, "isError", False)), result.content
        text = result.content[0].text
        return json.loads(text) if text.startswith("{") else text

    async def run():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert names >= {"analyze_diff", "get_impact", "explain", "get_review_context", "open_review_ui"}

            first = payload(await s.call_tool("analyze_diff", {}))
            assert first["reanalyzed"] is True
            helper = next(c for c in first["changed"] if c["qualname"] == "helper")
            assert helper["change"] == "signature"
            assert len(first["compile_errors"]) >= 2

            again = payload(await s.call_tool("analyze_diff", {}))
            assert again["reanalyzed"] is False

            imp = payload(await s.call_tool("get_impact", {"symbol": helper["id"]}))
            callers = {d["to"]["qualname"] for d in imp["downstream"]}
            assert {"a", "b", "c"} <= callers
            assert all(site["code"] for d in imp["downstream"] for site in d["sites"])

            path = payload(await s.call_tool("explain", {"from_symbol": "helper", "to_symbol": "a"}))
            assert path["hops"] == 1

            ctx = payload(await s.call_tool("get_review_context", {"budget_tokens": 500}))
            assert "CHANGED[signature] helper" in ctx

            # fix one caller: the warm server must see the edit and the error must go away
            app = repo / "fx" / "app.py"
            text = app.read_text(encoding="utf-8")
            app.write_text(text.replace("helper(1)", "helper(1, 2)", 1), encoding="utf-8")
            fixed = payload(await s.call_tool("analyze_diff", {}))
            assert fixed["reanalyzed"] is True
            assert len(fixed["compile_errors"]) == len(first["compile_errors"]) - 1

            ui = payload(await s.call_tool("open_review_ui", {}))
            assert ui["url"].startswith("http://127.0.0.1:")

    anyio.run(run)
