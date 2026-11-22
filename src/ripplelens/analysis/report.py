"""impact.json (schema ripplelens/impact@1), Markdown, and token-budgeted review context."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import __version__
from ..git.diff import DiffSpec
from . import changes as C
from .grouping import Grouper
from .impact import BLIND_SPOTS, Options, Traversal

SCHEMA = "ripplelens/impact@1"


def build_report(
    *,
    spec: DiffSpec,
    head_root: Path,
    file_changes: list[C.FileChanges],
    trav: Traversal,
    servers: list[dict[str, Any]],
    options: Options,
    seconds: float,
) -> dict[str, Any]:
    nodes_out = []
    impacted_by_seed: dict[str, int] = {}
    for n in trav.nodes.values():
        if n.change is None:
            for s in n.seeds:
                impacted_by_seed[s] = impacted_by_seed.get(s, 0) + 1
    for n in sorted(trav.nodes.values(), key=lambda n: (n.change is None, -n.score, n.path, n.start_line)):
        d: dict[str, Any] = {
            "id": n.id,
            "path": n.path,
            "name": n.name,
            "qualname": n.qualname,
            "kind": n.kind,
            "lines": [n.start_line + 1, n.end_line + 1],
            "change": n.change,
            "depth": n.depth,
            "score": round(n.score, 4),
            "via": n.via,
            "parent": n.parent,
            "seeds": sorted(n.seeds),
            "confidence": n.confidence,
            "reasons": n.reasons,
            "breaking": n.breaking,
            "truncated": n.truncated or None,
        }
        if n.change is not None and n.change_ref is not None:
            ch = n.change_ref
            d["signature"] = {"before": ch.sig_before, "after": ch.sig_after}
            d["renamed_from"] = ch.renamed_from
            d["lines_changed"] = ch.lines_changed
            d["on_base"] = n.on_base
            d["notes"] = ch.notes
            d["impacted"] = impacted_by_seed.get(n.id, 0)
            if n.on_base and ch.base is not None:
                d["lines"] = [ch.base.start_line + 1, ch.base.end_line + 1]
        nodes_out.append(d)

    paths: dict[str, list[str]] = {}
    for n in trav.nodes.values():
        if n.change is not None:
            continue
        chain = [n.id]
        cur = n
        guard = 0
        while cur.parent and guard < 50:
            chain.append(cur.parent)
            cur = trav.nodes.get(cur.parent)  # type: ignore[assignment]
            guard += 1
            if cur is None:
                break
        paths[n.id] = list(reversed(chain))

    files_out = []
    langs: set[str] = set()
    for fc in sorted(file_changes, key=lambda f: f.file.path):
        fd = fc.file
        if fc.language:
            langs.add(fc.language)
        seed_ids = [trav.node_id(_rel_of(fd, ch), ch.entity) for ch in fc.changes]
        impacted_files: dict[str, float] = {}
        for n in trav.nodes.values():
            if n.change is None and n.seeds & set(seed_ids):
                impacted_files[n.path] = max(impacted_files.get(n.path, 0.0), n.score)
        files_out.append(
            {
                "path": fd.path,
                "old_path": fd.old_path if fd.old_path != fd.new_path else None,
                "status": fd.status,
                "language": fc.language,
                "server": fc.server,
                "added": fd.added_lines,
                "removed": fd.removed_lines,
                "entities": seed_ids,
                "module_lines": _compress(fc.module_lines),
                "degraded": fc.degraded,
                "impacted_files": [
                    {"path": p, "score": round(s, 4)} for p, s in sorted(impacted_files.items(), key=lambda kv: -kv[1])
                ],
            }
        )

    by_class: dict[str, int] = {}
    for fc in file_changes:
        for ch in fc.changes:
            by_class[ch.change] = by_class.get(ch.change, 0) + 1
    impacted = [n for n in trav.nodes.values() if n.change is None]
    breaking = [n for n in trav.nodes.values() if n.breaking]
    blind = {lang: BLIND_SPOTS[lang] for lang in sorted(langs) if lang in BLIND_SPOTS}
    grouped = Grouper(head_root, Path(str(spec.repo)).name).build(
        [f["path"] for f in files_out] + [n["path"] for n in nodes_out]
    )
    return {
        "schema": SCHEMA,
        "tool": {"name": "ripplelens", "version": __version__},
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "repo": {
            "root": str(spec.repo),
            "analyzed_root": str(head_root),
            "base": {"ref": spec.base_label, "sha": spec.base},
            "head": {"ref": spec.head_label, "sha": spec.head},
        },
        "options": {k: getattr(options, k) for k in options.__dataclass_fields__},
        "servers": servers,
        "summary": {
            "files_changed": len(spec.files),
            "entities_changed": sum(by_class.values()),
            "by_change": by_class,
            "impacted": len(impacted),
            "impacted_files": len({n.path for n in impacted}),
            "breaking": len(breaking),
            "degraded": sum(1 for n in trav.nodes.values() if n.confidence == "degraded"),
            "truncated": bool(trav.truncation),
        },
        "files": files_out,
        "nodes": nodes_out,
        "edges": [
            {"source": e.source, "target": e.target, "kind": e.kind, "sites": e.sites} for e in trav.edges.values()
        ],
        "paths": paths,
        "groups": grouped["groups"],
        "locate": grouped["locate"],
        "truncation": trav.truncation,
        "blind_spots": blind,
        "warnings": trav.warnings[:50],
        "timing": {
            "total_seconds": round(seconds, 2),
            "requests": trav.stats["requests"],
            "request_seconds": round(trav.stats["request_seconds"], 2),
            "timeouts": trav.stats["timeouts"],
        },
    }


def _rel_of(fd, ch: C.EntityChange) -> str:
    return (fd.old_path if ch.head is None else fd.new_path) or fd.path


def _compress(lines: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for ln in lines:
        if out and ln == out[-1][1] + 1:
            out[-1][1] = ln
        else:
            out.append([ln, ln])
    return out


# ----------------------------------------------------------------------------- markdown

CHANGE_LABEL = {
    C.SIGNATURE: "signature changed",
    C.REMOVED: "removed",
    C.RENAMED: "renamed",
    C.VALUE: "declaration changed",
    C.TYPE: "type changed",
    C.BODY: "body changed",
    C.ADDED: "added",
    C.COSMETIC: "whitespace only",
}


def to_markdown(rep: dict[str, Any], max_impacted_per_seed: int = 15) -> str:
    s = rep["summary"]
    nodes = {n["id"]: n for n in rep["nodes"]}
    lines = [
        f"# ripplelens: {rep['repo']['base']['ref']} → {rep['repo']['head']['ref']}",
        "",
        f"**{s['files_changed']} files, {s['entities_changed']} changed entities, "
        f"{s['impacted']} impacted entities in {s['impacted_files']} files, {s['breaking']} with compile errors.**",
        "",
    ]
    bad = [sv for sv in rep["servers"] if sv.get("status") != "ok"]
    for sv in bad:
        lines.append(f"> ⚠ {sv['name']} {sv['status']}: {sv.get('reason') or ''}")
    if bad:
        lines.append("")
    breaking = [n for n in rep["nodes"] if n["breaking"]]
    if breaking:
        lines += ["## Compile errors at impacted sites", ""]
        for n in breaking:
            for b in n["breaking"]:
                lines.append(f"- `{n['path']}:{b['line']}` in `{n['qualname']}`: {b['message']}")
        lines.append("")
    lines += ["## Changes", ""]
    locate = rep.get("locate") or {}
    group_label = {g["id"]: f"{g['module']} / {g['package'] or '.'}" for g in rep.get("groups") or []}
    current_group = None
    for f in sorted(rep["files"], key=lambda f: ((locate.get(f["path"]) or {}).get("group", ""), f["path"])):
        gid = (locate.get(f["path"]) or {}).get("group")
        if group_label and gid != current_group:
            current_group = gid
            lines += [f"### {group_label.get(gid, gid)}", ""]
        level = "####" if group_label else "###"
        head = f"{level} `{f['path']}`" + (f" (renamed from `{f['old_path']}`)" if f.get("old_path") else "")
        lines.append(f"{head} — {f['status']} +{f['added']}/-{f['removed']}")
        for d in f["degraded"]:
            lines.append(f"> ⚠ {d}")
        for sid in f["entities"]:
            n = nodes.get(sid)
            if not n:
                continue
            label = CHANGE_LABEL.get(n["change"], n["change"])
            lines.append(f"- **{label}** `{n['qualname']}` ({n['kind']}, L{n['lines'][0]}–{n['lines'][1]})")
            sig = n.get("signature") or {}
            if n["change"] in (C.SIGNATURE, C.VALUE) and sig.get("before") and sig.get("after"):
                lines.append(f"  - before: `{sig['before']}`")
                lines.append(f"  - after:  `{sig['after']}`")
            if n.get("renamed_from"):
                lines.append(f"  - renamed from `{n['renamed_from']}`")
            imp = [x for x in rep["nodes"] if x["change"] is None and sid in x["seeds"]]
            imp.sort(key=lambda x: (-x["score"], x["path"]))
            for x in imp[:max_impacted_per_seed]:
                via = x["via"] or "?"
                conf = "" if x["confidence"] == "precise" else f" _{x['confidence']}_"
                lines.append(
                    f"  - ← `{x['qualname']}` `{x['path']}:{x['lines'][0]}` ({via}, depth {x['depth']},"
                    f" score {x['score']:.2f}){conf}"
                )
            if len(imp) > max_impacted_per_seed:
                lines.append(f"  - … {len(imp) - max_impacted_per_seed} more")
            if n.get("truncated"):
                for k, v in n["truncated"].items():
                    lines.append(f"  - ⚠ {k}: showing {v['shown']} of {v['total']} (hub)")
        if f["module_lines"]:
            ranges = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in f["module_lines"])
            lines.append(f"- module-level lines changed: {ranges}")
        lines.append("")
    if rep["truncation"]:
        lines += ["## Truncation", ""]
        for t in rep["truncation"][:20]:
            lines.append(f"- {t['reason']} at `{t['at']}`")
        lines.append("")
    if rep["blind_spots"]:
        lines += ["## Not visible to static analysis", ""]
        for lang, items in rep["blind_spots"].items():
            lines.append(f"- {lang}: " + "; ".join(items))
        lines.append("")
    return "\n".join(lines)


def review_context(
    rep: dict[str, Any],
    budget_tokens: int = 4000,
    scope: list[str] | None = None,
    read_lines: Callable[[str], list[str] | None] | None = None,
    diff_of: Callable[[str], list[tuple[int, int, int, int, list[str]]]] | None = None,
) -> str:
    """Prioritised plain-text context for an AI reviewer, cut to ~budget_tokens.

    With `scope` (entity ids) the context is structured per changed entity: signature
    before/after, the diff hunks inside it, direct callers with their call-site code,
    compile errors, confidence and the language's blind spots. `read_lines(path)` returns
    the head text of a file; `diff_of(path)` returns hunks as
    (old_start, old_len, new_start, new_len, lines).
    """
    if scope is not None:
        return _scoped_context(rep, scope, budget_tokens, read_lines or (lambda p: None), diff_of or (lambda p: []))
    budget = budget_tokens * 4
    s = rep["summary"]
    out = [
        f"ripplelens impact {rep['repo']['base']['ref']}..{rep['repo']['head']['ref']}: "
        f"{s['entities_changed']} changed, {s['impacted']} impacted, {s['breaking']} with compile errors.",
    ]
    nodes = rep["nodes"]
    order = (
        [n for n in nodes if n["breaking"]]
        + [n for n in nodes if n["change"] in (C.SIGNATURE, C.REMOVED, C.RENAMED, C.VALUE, C.TYPE)]
        + [n for n in nodes if n["change"] == C.BODY]
        + sorted([n for n in nodes if n["change"] is None and not n["breaking"]], key=lambda n: -n["score"])
    )
    seen: set[str] = set()
    used = len(out[0])
    for n in order:
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        if n["change"]:
            sig = n.get("signature") or {}
            line = f"CHANGED[{n['change']}] {n['qualname']} {n['path']}:{n['lines'][0]} impacted={n.get('impacted', 0)}"
            if sig.get("before") != sig.get("after") and sig.get("before") and sig.get("after"):
                line += f" sig: {sig['before']} -> {sig['after']}"
        else:
            path = " <- ".join(p.split("#")[1].split("@")[0] for p in rep["paths"].get(n["id"], [])[::-1])
            line = (
                f"IMPACTED {n['qualname']} {n['path']}:{n['lines'][0]} via={n['via']} depth={n['depth']} "
                f"score={n['score']:.2f} conf={n['confidence']} why: {path}"
            )
        for b in n["breaking"]:
            line += f"\n  ERROR {n['path']}:{b['line']}: {b['message']}"
        if used + len(line) + 1 > budget:
            out.append(f"... truncated to ~{budget_tokens} tokens; {len(nodes) - len(seen) + 1} entries omitted")
            break
        out.append(line)
        used += len(line) + 1
    if rep["truncation"]:
        out.append(f"note: traversal truncated at {len(rep['truncation'])} points (hub/budget/depth)")
    return "\n".join(out)


CONTRACT_CHANGES = (C.SIGNATURE, C.REMOVED, C.RENAMED, C.VALUE, C.TYPE)


def scope_seeds(rep: dict[str, Any], ids: list[str]) -> list[str]:
    """Changed entities a scope stands for: the changed ids themselves plus the seeds of impacted ids."""
    by_id = {n["id"]: n for n in rep["nodes"]}
    out: list[str] = []
    for i in ids:
        n = by_id.get(i)
        if n is None:
            continue
        for s in [i] if n["change"] else n["seeds"]:
            if s not in out and s in by_id:
                out.append(s)
    return out or [n["id"] for n in rep["nodes"] if n["change"]]


def _scoped_context(
    rep: dict[str, Any],
    ids: list[str],
    budget_tokens: int,
    read_lines: Callable[[str], list[str] | None],
    diff_of: Callable[[str], list[tuple[int, int, int, int, list[str]]]],
) -> str:
    by_id = {n["id"]: n for n in rep["nodes"]}
    out_edges: dict[str, list[dict[str, Any]]] = {}
    for e in rep["edges"]:
        out_edges.setdefault(e["source"], []).append(e)
    lang_of = {f["path"]: f["language"] for f in rep["files"]}
    seeds = scope_seeds(rep, ids)
    focus_impacted = [by_id[i] for i in ids if i in by_id and not by_id[i]["change"]]
    lines_cache: dict[str, list[str] | None] = {}

    def src(path: str) -> list[str] | None:
        if path not in lines_cache:
            lines_cache[path] = read_lines(path)
        return lines_cache[path]

    def excerpt(path: str, line: int, ctx: int = 1) -> list[str]:
        text = src(path)
        if not text:
            return []
        lo, hi = max(1, line - ctx), min(len(text), line + ctx)
        return [f"    {i:>5}{'>' if i == line else ' '} {text[i - 1].rstrip()}" for i in range(lo, hi + 1)]

    # (priority, text): lower priority numbers are kept first when the budget runs out
    blocks: list[tuple[int, str]] = []
    s = rep["summary"]
    blocks.append(
        (
            0,
            f"Change under review: {rep['repo']['base']['ref']}..{rep['repo']['head']['ref']} "
            f"({s['files_changed']} files, {s['entities_changed']} changed entities, {s['impacted']} impacted, "
            f"{s['breaking']} compile errors at impacted sites). Scope: {len(seeds)} changed entities.",
        )
    )
    langs: set[str] = set()
    for rank, sid in enumerate(seeds):
        n = by_id[sid]
        lang = lang_of.get(n["path"])
        if lang:
            langs.add(lang)
        head = [f"## CHANGED [{n['change']}] {n['qualname']} ({n['kind']}) {n['path']}:{n['lines'][0]}-{n['lines'][1]}"]
        sig = n.get("signature") or {}
        if sig.get("before") and sig.get("after") and sig["before"] != sig["after"]:
            head += [f"signature before: {sig['before']}", f"signature after:  {sig['after']}"]
        elif sig.get("after") or sig.get("before"):
            head.append(f"signature: {sig.get('after') or sig.get('before')}")
        if n.get("renamed_from"):
            head.append(f"renamed from: {n['renamed_from']}")
        head.append(f"confidence: {n['confidence']}" + (f" ({'; '.join(n['reasons'])})" if n["reasons"] else ""))
        for note in n.get("notes") or []:
            head.append(f"note: {note}")
        blocks.append((1 + rank, "\n".join(head)))

        start, end = n["lines"]
        hunk_lines: list[str] = []
        for o_start, o_len, n_start, n_len, body in diff_of(n["path"]):
            lo, hi = (o_start, o_start + max(o_len, 1) - 1) if n.get("on_base") else (n_start, n_start + max(n_len, 1) - 1)
            if hi < start or lo > end:
                continue
            hunk_lines.append(f"@@ -{o_start},{o_len} +{n_start},{n_len} @@")
            hunk_lines += body[:40]
        if hunk_lines:
            blocks.append((10 + rank, "diff inside this entity:\n" + "\n".join(hunk_lines[:80])))

        direct = [e for e in out_edges.get(sid, []) if e["target"] in by_id]
        direct.sort(key=lambda e: (not by_id[e["target"]]["breaking"], -by_id[e["target"]]["score"]))
        if direct:
            caller_lines = [f"direct callers / users ({len(direct)}):"]
            for e in direct[:12]:
                t = by_id[e["target"]]
                tag = f"[{t['change']}] " if t["change"] else ""
                conf = "" if t["confidence"] == "precise" else f" [{t['confidence']}]"
                caller_lines.append(f"- {tag}{t['qualname']} {t['path']}:{t['lines'][0]} via {e['kind']}{conf}")
                for b in t["breaking"]:
                    caller_lines.append(f"  COMPILE ERROR {t['path']}:{b['line']}: {b['message']}")
                for site in (e["sites"] or [])[:2]:
                    caller_lines += excerpt(t["path"], site)
            if len(direct) > 12:
                caller_lines.append(f"- … {len(direct) - 12} more")
            blocks.append((20 + rank, "\n".join(caller_lines)))

        further = [
            m for m in rep["nodes"] if not m["change"] and sid in m["seeds"] and m["depth"] >= 2
        ]
        if further:
            names = ", ".join(f"{m['qualname']} ({m['path']})" for m in sorted(further, key=lambda m: -m["score"])[:10])
            blocks.append((40 + rank, f"further impact (depth >= 2, {len(further)}): {names}"))

    for n in focus_impacted:
        path = " <- ".join(p.split("#")[1].split("@")[0] for p in rep["paths"].get(n["id"], [])[::-1])
        lines = [f"## FOCUSED IMPACTED {n['qualname']} {n['path']}:{n['lines'][0]} depth={n['depth']} why: {path}"]
        for b in n["breaking"]:
            lines.append(f"COMPILE ERROR {n['path']}:{b['line']}: {b['message']}")
            lines += excerpt(n["path"], b["line"], 2)
        blocks.append((5, "\n".join(lines)))

    blind = [f"- {lang}: " + "; ".join(rep["blind_spots"][lang]) for lang in sorted(langs) if lang in rep["blind_spots"]]
    if blind:
        blocks.append((30, "not visible to static analysis (do not claim certainty about these):\n" + "\n".join(blind)))
    if rep["truncation"]:
        blocks.append((50, f"note: traversal truncated at {len(rep['truncation'])} points; impact lists may be incomplete"))

    budget = budget_tokens * 4
    keep: set[int] = set()
    used = 0
    for i, (_, text) in sorted(enumerate(blocks), key=lambda x: x[1][0]):
        if used + len(text) + 2 > budget and keep:
            continue
        keep.add(i)
        used += len(text) + 2
    dropped = len(blocks) - len(keep)
    out = [text for i, (_, text) in enumerate(blocks) if i in keep]
    if dropped:
        out.append(f"(context cut to ~{budget_tokens} tokens; {dropped} sections omitted)")
    return "\n\n".join(out)
