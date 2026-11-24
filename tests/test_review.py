"""Agent pre-review store and the human-review HTTP endpoints."""

from __future__ import annotations

import copy
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ripplelens import review_store
from ripplelens.http_server import ReviewServer


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture()
def rep(tmp_path: Path) -> dict:
    root = tmp_path / "repo"
    (root / "core").mkdir(parents=True)
    (root / "core" / "m.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return {
        "repo": {"root": str(root), "analyzed_root": str(root), "base": {"ref": "HEAD", "sha": "a" * 40}, "head": {"ref": "working tree", "sha": None}},
        "files": [{"path": "core/m.py", "status": "M", "added": 1, "removed": 1}],
        "nodes": [{"id": "core/m.py#f@1", "path": "core/m.py", "qualname": "f", "change": "signature", "lines": [1, 2]}],
        "edges": [],
    }


FINDINGS = [
    {"severity": "low", "kind": "behavior", "title": "minor", "file": "core/m.py", "line": 2},
    {"severity": "critical", "kind": "architecture", "title": "core imports api", "file": "./core/m.py", "line": 1,
     "evidence": "deny core -> api", "suggestion": "move it"},
    {"title": ""},
]


def test_submit_normalizes_and_orders(rep):
    data = review_store.submit(rep["repo"]["root"], rep, "summary", "request_changes", FINDINGS)
    assert [f["title"] for f in data["findings"]] == ["core imports api", "minor"]
    top = data["findings"][0]
    assert (top["id"], top["severity"], top["path"], top["scope"], top["entity"]) == ("f1", "high", "core/m.py", "core", "core/m.py#f@1")
    assert review_store.store_path(rep["repo"]["root"]).is_file()
    state = review_store.with_state(review_store.load(rep["repo"]["root"]), rep)
    assert state["stale"] is False and state["counts"] == {"total": 2, "open": 2, "accepted": 0, "rejected": 0}


def test_review_goes_stale_when_the_change_moves(rep):
    review_store.submit(rep["repo"]["root"], rep, "s", "comment", FINDINGS[:1])
    moved = copy.deepcopy(rep)
    moved["files"][0]["added"] = 5
    assert review_store.with_state(review_store.load(rep["repo"]["root"]), moved)["stale"] is True


def test_update_and_markdown(rep):
    root = rep["repo"]["root"]
    review_store.submit(root, rep, "two issues", "request_changes", FINDINGS)
    review_store.update(root, {"findings": [{"id": "f1", "status": "accepted", "comment": "agreed"}, {"id": "f2", "status": "rejected"}],
                               "verdict": "request_changes", "note": "fix core"})
    md = review_store.to_markdown(review_store.load(root), rep)
    assert "Changes requested" in md and "fix core" in md
    assert md.index("Accepted findings") < md.index("core imports api") < md.index("Rejected findings") < md.index("minor")
    assert "reviewer: agreed" in md


def _post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def test_http_review_endpoints(rep):
    srv = ReviewServer(rep, 0)
    srv.start()
    base = srv.url.rstrip("/")
    try:
        assert json.loads(urllib.request.urlopen(base + "/api/review").read()) == {"review": None}
        review_store.submit(rep["repo"]["root"], rep, "s", "comment", FINDINGS)
        got = json.loads(urllib.request.urlopen(base + "/api/review").read())["review"]
        assert got["counts"]["open"] == 2
        with pytest.raises(urllib.error.HTTPError):
            _post(base + "/api/review", {"findings": [{"id": "f1", "status": "accepted"}]}, {"Origin": base})
        out = _post(base + "/api/review", {"findings": [{"id": "f1", "status": "accepted"}], "verdict": "approve"},
                    {"X-RippleLens-Token": srv.token, "Origin": base})["review"]
        assert out["counts"]["accepted"] == 1 and out["human"]["verdict"] == "approve"
        md = urllib.request.urlopen(base + "/api/review/markdown").read().decode()
        assert md.startswith("# Review") and "Approved" in md
    finally:
        srv.stop()
