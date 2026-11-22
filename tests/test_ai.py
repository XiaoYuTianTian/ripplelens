"""AI review: config, scoped context, finding parser, cache, and the HTTP/SSE endpoints (with a fake model)."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ripplelens.ai import ReviewEngine, load_config, save_config
from ripplelens.ai.engine import FindingStream, entity_index
from ripplelens.http_server import ReviewServer

BASE_LIB = "def f(x):\n    return x\n"
HEAD_LIB = "def f(x, y):\n    return x + y\n"
APP = "from lib import f\n\n\ndef a():\n    return f(1)\n\n\ndef e():\n    return a()\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def _node(nid, name, path, lines, change=None, depth=0, breaking=(), seeds=()):
    return {
        "id": nid, "path": path, "name": name, "qualname": name, "kind": "function", "lines": list(lines),
        "change": change, "depth": depth, "score": 1.0 / (depth + 1), "via": "call" if depth else None, "parent": None,
        "seeds": list(seeds), "confidence": "precise", "reasons": [], "breaking": list(breaking), "truncated": None,
        "signature": {"before": "def f(x)", "after": "def f(x, y)"} if change else None, "impacted": 2 if change else 0,
    }


@pytest.fixture()
def repo(tmp_path: Path) -> dict:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "lib.py").write_text(BASE_LIB, encoding="utf-8")
    (root / "app.py").write_text(APP, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    (root / "lib.py").write_text(HEAD_LIB, encoding="utf-8")
    sha = _git(root, "rev-parse", "HEAD").strip()
    seed = "lib.py#f@1"
    return {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "repo": {"root": str(root), "analyzed_root": str(root), "base": {"ref": "HEAD", "sha": sha}, "head": {"ref": "working tree", "sha": None}},
        "summary": {"files_changed": 1, "entities_changed": 1, "impacted": 2, "impacted_files": 1, "breaking": 1},
        "servers": [],
        "files": [{"path": "lib.py", "old_path": None, "language": "python", "degraded": []}],
        "nodes": [
            _node(seed, "f", "lib.py", (1, 2), change="signature"),
            _node("app.py#a@4", "a", "app.py", (4, 5), depth=1, seeds=[seed], breaking=[{"line": 5, "message": 'Argument missing for parameter "y"'}]),
            _node("app.py#e@8", "e", "app.py", (8, 9), depth=2, seeds=[seed]),
        ],
        "edges": [
            {"source": seed, "target": "app.py#a@4", "kind": "call", "sites": [5]},
            {"source": "app.py#a@4", "target": "app.py#e@8", "kind": "call", "sites": [9]},
        ],
        "paths": {"app.py#a@4": [seed, "app.py#a@4"], "app.py#e@8": [seed, "app.py#a@4", "app.py#e@8"]},
        "truncation": [],
        "blind_spots": {"python": ["getattr/setattr by string"]},
        "warnings": [],
    }


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLELENS_CONFIG_DIR", str(tmp_path / "cfg"))
    for var in ("RIPPLELENS_AI_PROVIDER", "RIPPLELENS_AI_BASE_URL", "RIPPLELENS_AI_MODEL", "RIPPLELENS_AI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_config_roundtrip_hides_key(monkeypatch):
    assert load_config().configured is False
    cfg = save_config({"base_url": "https://api.anthropic.com", "model": "claude-x", "api_key": "sk-secret", "enabled": True})
    assert cfg.provider == "anthropic" and cfg.configured and cfg.enabled
    pub = cfg.public()
    assert pub["has_key"] is True and "sk-secret" not in json.dumps(pub)
    # an empty key in an update keeps the stored one
    assert save_config({"model": "claude-y", "api_key": ""}).api_key == "sk-secret"
    monkeypatch.setenv("RIPPLELENS_AI_MODEL", "from-env")
    cfg = load_config()
    assert cfg.model == "from-env" and cfg.from_env == ["model"]


def test_local_endpoint_needs_no_key():
    cfg = save_config({"base_url": "http://127.0.0.1:11434/v1", "model": "qwen"})
    assert cfg.provider == "openai" and cfg.configured


def test_scoped_context_has_signature_diff_callers_and_errors(repo):
    ctx = ReviewEngine().context(repo, ["lib.py#f@1"])
    assert "signature before: def f(x)" in ctx and "signature after:  def f(x, y)" in ctx
    assert "+def f(x, y):" in ctx and "-def f(x):" in ctx  # diff hunk from git
    assert "app.py:4 via call" in ctx
    assert 'COMPILE ERROR app.py:5: Argument missing for parameter "y"' in ctx
    assert "return f(1)" in ctx  # call-site code
    assert "getattr/setattr by string" in ctx
    # focusing an impacted entity reviews its seed and names the focus
    ctx2 = ReviewEngine().context(repo, ["app.py#e@8"])
    assert "CHANGED [signature] f" in ctx2 and "FOCUSED IMPACTED e" in ctx2


def test_finding_stream_handles_split_lines_and_none(repo):
    idx = entity_index(repo)
    fs = FindingStream(idx)
    line = json.dumps({"severity": "critical", "title": "a() now fails", "file": "app.py", "line": 5, "evidence": "e", "suggestion": "s"})
    out = list(fs.feed(line[:20])) + list(fs.feed(line[20:] + "\nnoise\n")) + list(fs.close())
    assert len(out) == 1 and out[0]["severity"] == "high" and out[0]["entity"] == "app.py#a@4"
    fs = FindingStream(idx)
    assert list(fs.feed("NONE")) + list(fs.close()) == []


def test_review_streams_and_caches(repo):
    cfg = save_config({"base_url": "http://127.0.0.1:1/v1", "model": "fake", "enabled": True})
    calls = []
    finding = {"severity": "high", "title": "caller breaks", "file": "app.py", "line": 5, "evidence": "compile error", "suggestion": "pass y"}

    def chat(_cfg, system, user):
        calls.append((system, user))
        yield json.dumps(finding)[:30]
        yield json.dumps(finding)[30:] + "\n"

    eng = ReviewEngine()
    ev = list(eng.review(repo, cfg, ["lib.py#f@1"], lang="zh", chat=chat))
    assert [e["type"] for e in ev][0] == "start" and ev[-1]["type"] == "done"
    assert ev[-1]["findings"][0]["entity"] == "app.py#a@4"
    assert "Simplified Chinese" in calls[0][0]
    again = list(eng.review(repo, cfg, ["lib.py#f@1"], lang="zh", chat=chat))
    assert again[-1]["cached"] is True and len(calls) == 1
    list(eng.review(repo, cfg, ["lib.py#f@1"], lang="zh", force=True, chat=chat))
    assert len(calls) == 2


class _FakeModel(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["stream"] is True and self.path.endswith("/chat/completions")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        text = json.dumps({"severity": "medium", "title": "t", "file": "app.py", "line": 5, "evidence": "e", "suggestion": "s"}) + "\n"
        for i in range(0, len(text), 25):
            chunk = {"choices": [{"delta": {"content": text[i : i + 25]}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    return urllib.request.urlopen(req, timeout=20)


def test_http_endpoints_require_token_and_stream(repo):
    model = ThreadingHTTPServer(("127.0.0.1", 0), _FakeModel)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    srv = ReviewServer(repo, 0)
    srv.start()
    try:
        base = srv.url.rstrip("/")
        status = json.loads(urllib.request.urlopen(base + "/api/ai/status").read())
        assert status["configured"] is False and status["enabled"] is False
        html = urllib.request.urlopen(base + "/").read().decode()
        if srv.web_root is not None:
            assert f'content="{srv.token}"' in html
        with pytest.raises(urllib.error.HTTPError) as err:
            _post(base + "/api/ai/config", {"model": "x"}, {})
        assert err.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as err:
            _post(base + "/api/ai/config", {"model": "x"}, {"X-RippleLens-Token": srv.token, "Origin": "http://evil.example"})
        assert err.value.code == 403
        tok = {"X-RippleLens-Token": srv.token, "Origin": base}
        with pytest.raises(urllib.error.HTTPError) as err:
            _post(base + "/api/ai/review", {"ids": ["lib.py#f@1"]}, tok)
        assert err.value.code == 409  # not configured
        cfg = json.loads(
            _post(base + "/api/ai/config", {"base_url": f"http://127.0.0.1:{model.server_address[1]}/v1", "model": "fake", "enabled": True}, tok).read()
        )
        assert cfg["configured"] and "api_key" not in cfg
        resp = _post(base + "/api/ai/review", {"ids": ["lib.py#f@1"], "lang": "en"}, tok)
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        events = [json.loads(line[5:]) for line in resp.read().decode().splitlines() if line.startswith("data:")]
        assert events[-1]["type"] == "done" and events[-1]["findings"][0]["path"] == "app.py"
        assert any(e["type"] == "delta" for e in events)
    finally:
        srv.stop()
        model.shutdown()
