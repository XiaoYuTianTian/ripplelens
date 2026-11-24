"""Agent pre-review + human verdicts, stored next to the repository in <git-dir>/ripplelens/review.json.

The agent writes findings with `submit`; the review UI records a status (accepted / rejected) and a comment
per finding plus an overall verdict with `update`. A review belongs to one version of the change: when the
diff it was written for no longer matches the current report, the review is flagged stale.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import posixpath
import threading
from pathlib import Path
from typing import Any

from .git import diff as gitdiff

SEVERITIES = ("high", "medium", "low")
KINDS = ("architecture", "contract", "behavior")
AGENT_VERDICTS = ("approve", "comment", "request_changes")
HUMAN_STATUS = ("open", "accepted", "rejected")
HUMAN_VERDICTS = ("approve", "request_changes")
MAX_FINDINGS = 50

_lock = threading.Lock()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def store_path(repo: str | Path) -> Path:
    root = Path(repo)
    git_dir = gitdiff.git(root, "rev-parse", "--git-common-dir", check=False).strip() or ".git"
    p = Path(git_dir)
    if not p.is_absolute():
        p = root / p
    return p / "ripplelens" / "review.json"


def content_signature(rep: dict[str, Any]) -> str:
    """Identity of the change a review talks about; stable across re-analysis of the same diff."""
    h = hashlib.sha1()
    h.update(f"{rep['repo']['base'].get('sha')}..{rep['repo']['head'].get('sha')}".encode())
    for f in sorted(rep["files"], key=lambda f: f["path"]):
        h.update(f"{f['path']}:{f.get('status')}:{f.get('added')}:{f.get('removed')}".encode())
    for n in sorted((n for n in rep["nodes"] if n["change"]), key=lambda n: n["id"]):
        h.update(f"{n['id']}:{n['change']}:{n['lines'][0]}:{n['lines'][1]}".encode())
    return h.hexdigest()[:16]


def load(repo: str | Path) -> dict[str, Any] | None:
    p = store_path(repo)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(repo: str | Path, data: dict[str, Any]) -> None:
    p = store_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def with_state(review: dict[str, Any] | None, rep: dict[str, Any]) -> dict[str, Any] | None:
    """The stored review plus `stale` and counts, as the UI and the MCP tools see it."""
    if review is None:
        return None
    out = dict(review)
    out["stale"] = review.get("signature") != content_signature(rep)
    fs = review.get("findings") or []
    out["counts"] = {
        "total": len(fs),
        "open": sum(1 for f in fs if f.get("status", "open") == "open"),
        "accepted": sum(1 for f in fs if f.get("status") == "accepted"),
        "rejected": sum(1 for f in fs if f.get("status") == "rejected"),
    }
    return out


def _entity_finder(rep: dict[str, Any]):
    from .ai.engine import entity_index

    return entity_index(rep)


def _norm_finding(raw: dict[str, Any], i: int, find) -> dict[str, Any] | None:
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    sev = str(raw.get("severity") or "medium").lower()
    sev = {"critical": "high", "blocker": "high", "major": "high", "warning": "medium", "minor": "low", "info": "low"}.get(sev, sev)
    kind = str(raw.get("kind") or "behavior").lower()
    path = str(raw.get("file") or raw.get("path") or "").replace("\\", "/").lstrip("./")
    try:
        line = int(raw.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    scope = str(raw.get("scope") or "").replace("\\", "/").strip("/") or (posixpath.dirname(path) if path else "")
    return {
        "id": f"f{i + 1}",
        "severity": sev if sev in SEVERITIES else "medium",
        "kind": kind if kind in KINDS else "behavior",
        "title": title[:300],
        "path": path,
        "line": line,
        "scope": scope,
        "entity": (find(path, line) if path and line else None),
        "evidence": str(raw.get("evidence") or "").strip()[:2000],
        "suggestion": str(raw.get("suggestion") or "").strip()[:2000],
        "status": "open",
        "comment": "",
    }


def submit(repo: str | Path, rep: dict[str, Any], summary: str, verdict: str, findings: list[dict[str, Any]], author: str = "agent") -> dict[str, Any]:
    find = _entity_finder(rep)
    norm = [f for f in (_norm_finding(x, i, find) for i, x in enumerate((findings or [])[:MAX_FINDINGS]) if isinstance(x, dict)) if f]
    order = {s: i for i, s in enumerate(SEVERITIES)}
    norm.sort(key=lambda f: order[f["severity"]])
    for i, f in enumerate(norm):
        f["id"] = f"f{i + 1}"
    data = {
        "schema": "ripplelens/review@1",
        "signature": content_signature(rep),
        "range": f"{rep['repo']['base']['ref']}..{rep['repo']['head']['ref']}",
        "agent": {
            "author": author,
            "summary": str(summary or "").strip()[:4000],
            "verdict": verdict if verdict in AGENT_VERDICTS else "comment",
            "submitted_at": _now(),
        },
        "findings": norm,
        "human": {"verdict": None, "note": "", "updated_at": None},
    }
    with _lock:
        _write(repo, data)
    return data


def update(repo: str | Path, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Apply human decisions: {"findings": [{"id", "status", "comment"}], "verdict", "note"}."""
    with _lock:
        data = load(repo)
        if data is None:
            return None
        by_id = {f["id"]: f for f in data.get("findings") or []}
        for ch in changes.get("findings") or []:
            f = by_id.get(str(ch.get("id")))
            if f is None:
                continue
            if ch.get("status") in HUMAN_STATUS:
                f["status"] = ch["status"]
            if "comment" in ch:
                f["comment"] = str(ch.get("comment") or "")[:4000]
        human = data.setdefault("human", {"verdict": None, "note": "", "updated_at": None})
        if "verdict" in changes:
            v = changes.get("verdict")
            human["verdict"] = v if v in HUMAN_VERDICTS else None
        if "note" in changes:
            human["note"] = str(changes.get("note") or "")[:8000]
        human["updated_at"] = _now()
        _write(repo, data)
        return data


SEV_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}
STATUS_LABEL = {"open": "not reviewed", "accepted": "accepted", "rejected": "rejected"}


def to_markdown(review: dict[str, Any] | None, rep: dict[str, Any]) -> str:
    if not review:
        return "# Review\n\nNo agent pre-review yet. Run `/ripplelens:review` in Claude Code.\n"
    r = with_state(review, rep) or review
    human = r.get("human") or {}
    verdict = {"approve": "Approved", "request_changes": "Changes requested"}.get(human.get("verdict") or "", "Pending")
    lines = [
        f"# Review: {r.get('range', '')}",
        "",
        f"**Verdict: {verdict}**" + (f" — {human['note']}" if human.get("note") else ""),
        "",
    ]
    if r.get("stale"):
        lines += ["> ⚠ The code changed after the agent pre-review; findings may be out of date.", ""]
    agent = r.get("agent") or {}
    lines += [f"Agent pre-review ({agent.get('verdict', 'comment')}, {agent.get('submitted_at', '')}):", "", agent.get("summary") or "_no summary_", ""]
    groups = [("accepted", "Accepted findings"), ("open", "Not yet reviewed"), ("rejected", "Rejected findings")]
    for status, title in groups:
        fs = [f for f in r.get("findings") or [] if f.get("status", "open") == status]
        if not fs:
            continue
        lines += [f"## {title}", ""]
        for f in fs:
            at = f" `{f['path']}:{f['line']}`" if f.get("path") else ""
            lines.append(f"- **[{SEV_LABEL.get(f['severity'], f['severity'])} · {f['kind']}]** {f['title']}{at}")
            if f.get("evidence"):
                lines.append(f"  - evidence: {f['evidence']}")
            if f.get("suggestion"):
                lines.append(f"  - suggestion: {f['suggestion']}")
            if f.get("comment"):
                lines.append(f"  - reviewer: {f['comment']}")
        lines.append("")
    return "\n".join(lines)
