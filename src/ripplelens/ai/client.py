"""Streaming chat clients for OpenAI-compatible and Anthropic Messages APIs (urllib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

from .config import AiConfig


class AiError(RuntimeError):
    pass


def _request(cfg: AiConfig, system: str, user: str, max_tokens: int) -> urllib.request.Request:
    base = cfg.endpoint
    if cfg.provider == "anthropic":
        url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        body = {
            "model": cfg.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "stream": True,
        }
        headers = {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"}
    else:
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        body = {
            "model": cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    headers |= {"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "ripplelens"}
    return urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")


def stream_chat(cfg: AiConfig, system: str, user: str, *, max_tokens: int = 2048, timeout: float = 180) -> Iterator[str]:
    """Yield text deltas. Raises AiError with the server's message on failure."""
    req = _request(cfg, system, user, max_tokens)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read(2000).decode("utf-8", "replace")
        try:
            j = json.loads(detail)
            detail = (j.get("error") or {}).get("message") or j.get("message") or detail
        except ValueError:
            pass
        raise AiError(f"HTTP {e.code} from {req.full_url}: {detail.strip()[:400]}") from None
    except (urllib.error.URLError, OSError) as e:
        raise AiError(f"cannot reach {req.full_url}: {getattr(e, 'reason', e)}") from None
    with resp:
        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" not in ctype and "json" in ctype:
            # server ignored stream=true
            yield from _from_json(cfg, json.loads(resp.read().decode("utf-8", "replace")))
            return
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            text = _delta(cfg, ev)
            if text:
                yield text


def _delta(cfg: AiConfig, ev: dict) -> str | None:
    if ev.get("type") == "error" or "error" in ev and not ev.get("choices"):
        err = ev.get("error") or {}
        raise AiError(err.get("message") if isinstance(err, dict) else str(err))
    if cfg.provider == "anthropic":
        if ev.get("type") == "content_block_delta":
            return (ev.get("delta") or {}).get("text")
        return None
    choices = ev.get("choices") or []
    if not choices:
        return None
    return (choices[0].get("delta") or {}).get("content")


def _from_json(cfg: AiConfig, doc: dict) -> Iterator[str]:
    if cfg.provider == "anthropic":
        for block in doc.get("content") or []:
            if block.get("type") == "text":
                yield block.get("text", "")
    else:
        for ch in doc.get("choices") or []:
            yield (ch.get("message") or {}).get("content") or ""
