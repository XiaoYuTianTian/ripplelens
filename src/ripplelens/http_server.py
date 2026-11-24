"""Local review UI server (127.0.0.1 only).

GET /                     prebuilt web UI (ripplelens/web_dist)
GET /api/impact           current impact.json
GET /api/tree             repo file tree (git ls-files) for the heat directory
GET /api/source?path=&start=&end=   source excerpt of a file that appears in the report
GET /api/markdown         Markdown report
GET /api/context?budget=  token-budgeted review context for an AI reviewer
GET /api/ai/status        AI reviewer settings (never the key)
POST /api/ai/config       update AI reviewer settings
POST /api/ai/review       stream a review of a scope as server-sent events

Every /api/ai/* request must come from this server's own page: the Host and Origin headers
must name 127.0.0.1/localhost on this port, and POSTs carry the per-launch session token
that is only embedded in the served index.html.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

FALLBACK_HTML = """<!doctype html><meta charset="utf-8"><title>ripplelens</title>
<body style="font-family:system-ui;background:#0b0d12;color:#e6e8ee;padding:2rem">
<h1>ripplelens</h1><p>The web UI is not built. Run <code>npm --prefix web run build</code>.</p>
<p>Raw report: <a style="color:#7aa2ff" href="/api/impact">/api/impact</a></p></body>"""


def _web_root() -> Path | None:
    try:
        root = Path(str(resources.files("ripplelens") / "web_dist"))
    except (ModuleNotFoundError, TypeError):
        return None
    return root if (root / "index.html").is_file() else None


class ReviewServer:
    def __init__(self, report: dict[str, Any], port: int = 0):
        from .ai import ReviewEngine

        self._report = report
        self._lock = threading.Lock()
        self.web_root = _web_root()
        self.token = secrets.token_urlsafe(24)
        self.ai = ReviewEngine()
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/"

    def update(self, report: dict[str, Any]) -> None:
        with self._lock:
            self._report = report

    def report(self) -> dict[str, Any]:
        with self._lock:
            return self._report

    def allowed_files(self) -> dict[str, Path]:
        rep = self.report()
        root = Path(rep["repo"]["analyzed_root"])
        rels = {n["path"] for n in rep["nodes"]} | {f["path"] for f in rep["files"]}
        return {r: (root / r) for r in rels}

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="ripplelens-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # silence
                return

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                url = urlparse(self.path)
                if url.path == "/api/impact":
                    body = json.dumps(server.report(), ensure_ascii=False).encode("utf-8")
                    return self._send(200, body, "application/json; charset=utf-8")
                if url.path == "/api/tree":
                    from .repo_tree import tree_payload

                    root = Path(server.report()["repo"]["analyzed_root"])
                    return self._send(
                        200,
                        json.dumps(tree_payload(root), ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8",
                    )
                if url.path == "/api/source":
                    return self._source(parse_qs(url.query))
                if url.path == "/api/markdown":
                    from .analysis.report import to_markdown

                    return self._send(200, to_markdown(server.report()).encode("utf-8"), "text/markdown; charset=utf-8")
                if url.path == "/api/context":
                    from .analysis.report import review_context

                    q = parse_qs(url.query)
                    budget = int((q.get("budget") or ["4000"])[0])
                    body = review_context(server.report(), budget).encode("utf-8")
                    return self._send(200, body, "text/plain; charset=utf-8")
                if url.path in ("/api/review", "/api/review/markdown"):
                    if not self._same_origin():
                        return self._json(403, {"error": "forbidden"})
                    from . import review_store

                    rep = server.report()
                    review = review_store.load(rep["repo"]["root"])
                    if url.path == "/api/review/markdown":
                        return self._send(200, review_store.to_markdown(review, rep).encode("utf-8"), "text/markdown; charset=utf-8")
                    return self._json(200, {"review": review_store.with_state(review, rep)})
                if url.path == "/api/ai/status":
                    if not self._same_origin():
                        return self._json(403, {"error": "forbidden"})
                    from .ai import load_config

                    return self._json(200, load_config().public())
                return self._static(url.path)

            # ---------------------------------------------------------------- AI endpoints

            def _same_origin(self) -> bool:
                port = server.httpd.server_address[1]
                hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
                if self.headers.get("Host", "") not in hosts:
                    return False
                origin = self.headers.get("Origin")
                return origin is None or origin in {f"http://{h}" for h in hosts}

            def _json(self, code: int, obj: Any) -> None:
                self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

            def do_POST(self) -> None:
                url = urlparse(self.path)
                if url.path not in ("/api/ai/config", "/api/ai/review", "/api/review"):
                    return self._json(404, {"error": "not found"})
                token = self.headers.get("X-RippleLens-Token", "")
                if not self._same_origin() or not hmac.compare_digest(token, server.token):
                    return self._json(403, {"error": "forbidden: open the review UI from the URL ripplelens printed"})
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(min(length, 1 << 20)) or b"{}")
                except ValueError:
                    return self._json(400, {"error": "invalid JSON"})
                if url.path == "/api/review":
                    from . import review_store

                    rep = server.report()
                    if not isinstance(body, dict):
                        return self._json(400, {"error": "expected an object"})
                    data = review_store.update(rep["repo"]["root"], body)
                    if data is None:
                        return self._json(404, {"error": "no agent pre-review to update"})
                    return self._json(200, {"review": review_store.with_state(data, rep)})
                from .ai import load_config, save_config

                if url.path == "/api/ai/config":
                    return self._json(200, save_config(body).public())
                cfg = load_config()
                if not cfg.configured:
                    return self._json(409, {"error": "AI review is not configured"})
                if not cfg.enabled:
                    return self._json(409, {"error": "AI review is turned off"})
                ids = [str(i) for i in body.get("ids") or []][:500]
                lang = "zh" if str(body.get("lang", "")).startswith("zh") else "en"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                events = server.ai.review(server.report(), cfg, ids, lang=lang, force=bool(body.get("force")))
                try:
                    for ev in events:
                        self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    events.close()
                return None

            def _source(self, q: dict[str, list[str]]) -> None:
                rel = (q.get("path") or [""])[0]
                allowed = server.allowed_files()
                if rel not in allowed:
                    return self._send(404, b'{"error":"not in report"}', "application/json")
                try:
                    lines = allowed[rel].read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    return self._send(404, b'{"error":"unreadable"}', "application/json")
                start = max(1, int((q.get("start") or ["1"])[0]))
                end = min(len(lines), int((q.get("end") or [str(start + 40)])[0]), start + 400)
                body = json.dumps({"path": rel, "start": start, "lines": lines[start - 1 : end]}, ensure_ascii=False)
                return self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")

            def _static(self, path: str) -> None:
                root = server.web_root
                if root is None:
                    return self._send(200, FALLBACK_HTML.encode(), "text/html; charset=utf-8")
                rel = path.lstrip("/") or "index.html"
                target = (root / rel).resolve()
                if not str(target).startswith(str(root.resolve())) or not target.is_file():
                    target = root / "index.html"
                ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript",):
                    ctype += "; charset=utf-8"
                data = target.read_bytes()
                if target.name == "index.html":
                    meta = f'<meta name="rl-token" content="{server.token}" />'.encode()
                    data = data.replace(b"</head>", meta + b"</head>", 1)
                return self._send(HTTPStatus.OK, data, ctype)

        return Handler


def serve(report: dict[str, Any], port: int = 0, open_browser: bool = True, block: bool = True) -> ReviewServer:
    srv = ReviewServer(report, port)
    srv.start()
    print(f"ripplelens: review UI at {srv.url}", flush=True)
    if open_browser and not os.environ.get("RIPPLELENS_NO_BROWSER"):
        webbrowser.open(srv.url)
    if block:
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            srv.stop()
    return srv
