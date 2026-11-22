"""A local OpenAI-compatible model stub for demos and screenshots (no API key, no network).

  python eval/fake_model.py [--port 8799]
  RIPPLELENS_AI_BASE_URL=http://127.0.0.1:8799/v1 RIPPLELENS_AI_MODEL=demo-reviewer ripplelens review

It streams findings derived from the context it receives: each compile error becomes a
"high" finding and each contract change with callers becomes a "medium" one, so the UI
shows realistic output. It is not a reviewer.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ERR = re.compile(r"COMPILE ERROR (\S+):(\d+): (.+)")
CHANGED = re.compile(r"## CHANGED \[(signature|value|removed|renamed|type)\] (\S+) .* (\S+):(\d+)-\d+")


def findings(prompt: str, zh: bool) -> list[dict]:
    out, seen = [], set()
    for path, line, msg in ERR.findall(prompt):
        if (path, line) in seen:
            continue
        seen.add((path, line))
        out.append(
            {
                "severity": "high",
                "title": f"{path.rsplit('/', 1)[-1]}:{line} 不再能编译" if zh else f"{path.rsplit('/', 1)[-1]}:{line} no longer compiles",
                "file": path,
                "line": int(line),
                "evidence": msg,
                "suggestion": "按新签名更新这个调用处，或为旧签名保留一个兼容重载。" if zh else "Update this call to the new signature, or keep an overload for the old one.",
            }
        )
    for kind, name, path, line in CHANGED.findall(prompt):
        if kind == "value":
            out.append(
                {
                    "severity": "medium",
                    "title": f"{name} 的取值变化会影响所有读取方" if zh else f"Changing {name} affects every reader",
                    "file": path,
                    "line": int(line),
                    "evidence": "上下文列出了直接读取这个常量的函数。" if zh else "The context lists functions that read this constant directly.",
                    "suggestion": "确认这些读取方的测试覆盖了新值，尤其是金额计算。" if zh else "Check that the listed readers are covered for the new value, especially money calculations.",
                }
            )
    return out[:6]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        msgs = body.get("messages") or []
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        prompt = next((m["content"] for m in msgs if m["role"] == "user"), "")
        items = findings(prompt, "Chinese" in system)
        text = "\n".join(json.dumps(f, ensure_ascii=False) for f in items) or "NONE"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(text), 12):
            chunk = {"choices": [{"delta": {"content": text[i : i + 12]}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.03)
        self.wfile.write(b"data: [DONE]\n\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake model at http://127.0.0.1:{args.port}/v1", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
