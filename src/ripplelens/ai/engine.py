"""Scope review: build the structured context, stream the model, parse findings, cache results."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ..analysis.report import review_context, scope_seeds
from .client import AiError, stream_chat
from .config import AiConfig

SEVERITIES = ("high", "medium", "low")
_SEV_ALIASES = {"critical": "high", "blocker": "high", "major": "high", "error": "high", "warning": "medium", "minor": "low", "info": "low"}
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

SYSTEM = """You review a code change. The context comes from a compiler-backed language server: changed \
entities with their signature before/after and diff, their direct callers with the call-site code, \
compile errors the compiler reports at impacted sites, and how confident the analysis is.

Report only problems a careful reviewer would block or fix, and only when the context shows the evidence:
- callers that break or silently change behavior because of the change (wrong arguments, changed units, \
changed defaults, removed cases, new exceptions, changed return shape);
- call sites the change forgot to update; compile errors and what they mean for the change;
- shared contracts (constants, types, public functions) whose change reaches code that relies on the old value;
- concrete edge cases visible in the diff (off-by-one, null/empty input, rounding, overflow, ordering).

Never:
- restate, summarize or praise the change;
- comment on style, naming, formatting, comments or docs;
- give generic advice ("add tests", "consider logging") unless a specific untested caller is shown;
- speculate about code that is not in the context; respect the listed blind spots.

Output format: one JSON object per line, nothing else (no prose, no markdown fences):
{"severity":"high|medium|low","title":"<one line>","file":"<path from the context>","line":<number from the context>,"evidence":"<what in the context proves it>","suggestion":"<the concrete fix>"}
At most 6 findings, most severe first. If nothing qualifies, output exactly: NONE
Write title, evidence and suggestion in {language}."""

LANG_NAME = {"zh": "Simplified Chinese", "en": "English"}


# ---------------------------------------------------------------------------- context providers


class RepoSource:
    """Head text and diff hunks for files in the report, read lazily from the repo on disk."""

    def __init__(self, rep: dict[str, Any]):
        self.root = Path(rep["repo"]["analyzed_root"])
        self.repo = Path(rep["repo"]["root"])
        self.base = rep["repo"]["base"]["sha"]
        self.head = rep["repo"]["head"]["sha"]
        self.allowed = {n["path"] for n in rep["nodes"]} | {f["path"] for f in rep["files"]}
        self.old_path = {f["path"]: f.get("old_path") for f in rep["files"]}
        self._diff: dict[str, list[tuple[int, int, int, int, list[str]]]] = {}

    def read_lines(self, rel: str) -> list[str] | None:
        if rel not in self.allowed:
            return None
        try:
            return (self.root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

    def diff_of(self, rel: str) -> list[tuple[int, int, int, int, list[str]]]:
        if rel in self._diff:
            return self._diff[rel]
        hunks: list[tuple[int, int, int, int, list[str]]] = []
        if rel in self.old_path:
            paths = [p for p in (self.old_path.get(rel), rel) if p]
            cmd = ["git", "-C", str(self.repo), "diff", "--no-color", "--no-ext-diff", "-U2", "-M", self.base]
            if self.head:
                cmd.append(self.head)
            try:
                text = subprocess.run(cmd + ["--", *paths], capture_output=True, timeout=30, check=False).stdout.decode(
                    "utf-8", "replace"
                )
            except (OSError, subprocess.SubprocessError):
                text = ""
            cur: list[str] | None = None
            for line in text.splitlines():
                m = _HUNK.match(line)
                if m:
                    o, ol, n, nl = m.groups()
                    cur = []
                    hunks.append((int(o), int(ol or 1), int(n), int(nl or 1), cur))
                elif cur is not None and line[:1] in "+- ":
                    cur.append(line)
        self._diff[rel] = hunks
        return hunks


# ---------------------------------------------------------------------------- parsing


def parse_finding(line: str, rep_index: Callable[[str, int], str | None]) -> dict[str, Any] | None:
    s = line.strip().strip("`").strip()
    if s.startswith(("- ", "* ")):
        s = s[2:]
    if not s.startswith("{"):
        return None
    try:
        d = json.loads(s)
    except ValueError:
        return None
    if not isinstance(d, dict) or not d.get("title"):
        return None
    sev = str(d.get("severity", "medium")).lower()
    sev = _SEV_ALIASES.get(sev, sev)
    if sev not in SEVERITIES:
        sev = "medium"
    try:
        line_no = int(d.get("line") or 0)
    except (TypeError, ValueError):
        line_no = 0
    path = str(d.get("file") or "").replace("\\", "/").lstrip("./")
    return {
        "severity": sev,
        "title": str(d["title"]).strip(),
        "path": path,
        "line": line_no,
        "evidence": str(d.get("evidence") or "").strip(),
        "suggestion": str(d.get("suggestion") or "").strip(),
        "entity": rep_index(path, line_no),
    }


class FindingStream:
    """Feed text deltas; yields complete findings as their lines finish."""

    def __init__(self, rep_index: Callable[[str, int], str | None]):
        self.buf = ""
        self.text = ""
        self.index = rep_index

    def feed(self, delta: str) -> Iterator[dict[str, Any]]:
        self.text += delta
        self.buf += delta
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            f = parse_finding(line, self.index)
            if f:
                yield f

    def close(self) -> Iterator[dict[str, Any]]:
        if self.buf.strip():
            f = parse_finding(self.buf, self.index)
            self.buf = ""
            if f:
                yield f


def entity_index(rep: dict[str, Any]) -> Callable[[str, int], str | None]:
    by_path: dict[str, list[dict[str, Any]]] = {}
    for n in rep["nodes"]:
        by_path.setdefault(n["path"], []).append(n)

    def find(path: str, line: int) -> str | None:
        cands = by_path.get(path)
        if not cands:
            cands = next((v for k, v in by_path.items() if k.endswith("/" + path) or path.endswith("/" + k)), None)
        if not cands:
            return None
        inside = [n for n in cands if n["lines"][0] <= line <= n["lines"][1]]
        if inside:
            return min(inside, key=lambda n: n["lines"][1] - n["lines"][0])["id"]
        return None

    return find


# ---------------------------------------------------------------------------- engine


def report_signature(rep: dict[str, Any]) -> str:
    h = hashlib.sha1()
    h.update(rep.get("generated_at", "").encode())
    h.update(json.dumps(rep["summary"], sort_keys=True).encode())
    h.update(json.dumps([(n["id"], n["change"], n["lines"]) for n in rep["nodes"]]).encode())
    return h.hexdigest()[:16]


class ReviewEngine:
    def __init__(self, budget_tokens: int = 6000):
        self.budget = budget_tokens
        self._cache: dict[tuple[str, ...], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def context(self, rep: dict[str, Any], ids: list[str]) -> str:
        src = RepoSource(rep)
        return review_context(rep, self.budget, scope=ids, read_lines=src.read_lines, diff_of=src.diff_of)

    def key(self, rep: dict[str, Any], cfg: AiConfig, ids: list[str], lang: str) -> tuple[str, ...]:
        seeds = ",".join(sorted(scope_seeds(rep, ids)))
        focus = ",".join(sorted(i for i in ids if i not in seeds))
        return (report_signature(rep), cfg.provider, cfg.endpoint, cfg.model, lang, seeds, focus)

    def cached(self, key: tuple[str, ...]) -> dict[str, Any] | None:
        with self._lock:
            return self._cache.get(key)

    def review(
        self,
        rep: dict[str, Any],
        cfg: AiConfig,
        ids: list[str],
        lang: str = "en",
        force: bool = False,
        chat: Callable[[AiConfig, str, str], Iterator[str]] = stream_chat,
    ) -> Iterator[dict[str, Any]]:
        """Yield events: start, delta, finding, done (or error)."""
        key = self.key(rep, cfg, ids, lang)
        hit = None if force else self.cached(key)
        if hit:
            yield {"type": "start", "model": cfg.model, "tokens": hit["tokens"], "cached": True}
            for f in hit["findings"]:
                yield {"type": "finding", "finding": f}
            yield {"type": "done", "findings": hit["findings"], "cached": True}
            return
        ctx = self.context(rep, ids)
        tokens = len(ctx) // 4
        yield {"type": "start", "model": cfg.model, "tokens": tokens, "cached": False}
        system = SYSTEM.replace("{language}", LANG_NAME.get(lang, "English"))
        parser = FindingStream(entity_index(rep))
        findings: list[dict[str, Any]] = []
        try:
            for delta in chat(cfg, system, ctx):
                yield {"type": "delta", "text": delta}
                for f in parser.feed(delta):
                    findings.append(f)
                    yield {"type": "finding", "finding": f}
            for f in parser.close():
                findings.append(f)
                yield {"type": "finding", "finding": f}
        except AiError as e:
            yield {"type": "error", "message": str(e)}
            return
        order = {s: i for i, s in enumerate(SEVERITIES)}
        findings.sort(key=lambda f: order[f["severity"]])
        with self._lock:
            self._cache[key] = {"findings": findings, "tokens": tokens}
        yield {"type": "done", "findings": findings, "cached": False}
