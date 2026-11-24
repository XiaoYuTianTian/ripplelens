"""diff -> entities -> traversal -> impact report."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..git import diff as gitdiff
from ..git.worktree import worktree_for
from ..lsp.pool import ServerHandle, ServerPool, ServerUnavailable
from ..lsp.registry import LANGUAGE_OF_SERVER, Registry, ServerSpec
from . import changes as C
from .impact import Options, Traversal
from .report import build_report

log = logging.getLogger("ripplelens")

LANGUAGE_OF_EXT = {
    ".go": "go", ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".py": "python", ".pyi": "python", ".java": "java",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".c++": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".hxx": "cpp", ".ipp": "cpp", ".inl": "cpp",
}


def language_of(path: str) -> str | None:
    return LANGUAGE_OF_EXT.get(Path(path).suffix.lower())


def analyze(
    repo: str | os.PathLike[str],
    base: str | None = None,
    head: str | None = None,
    options: Options | None = None,
    pool: ServerPool | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    t_start = time.monotonic()
    opt = options or Options()
    say = progress or (lambda msg: log.info(msg))
    spec = gitdiff.resolve(repo, base, head)
    own_pool = pool is None
    pool = pool or ServerPool(Registry.load(spec.repo))
    head_root = spec.repo
    if spec.head is not None:
        cur = gitdiff.rev_parse(spec.repo, "HEAD")
        if not (cur == spec.head and not gitdiff.is_dirty(spec.repo)):
            head_root = worktree_for(spec.repo, spec.head)
    extra = f", skipped {spec.untracked_skipped} untracked noise paths" if spec.untracked_skipped else ""
    say(f"comparing {spec.base_label} ({spec.base[:10]}) .. {spec.head_label}: {len(spec.files)} files{extra}")

    groups: dict[str, list[gitdiff.FileDiff]] = {}
    unsupported: list[gitdiff.FileDiff] = []
    for fd in spec.files:
        sspec = pool.registry.for_path(fd.path)
        if sspec is None or fd.binary:
            unsupported.append(fd)
        else:
            groups.setdefault(sspec.name, []).append(fd)

    handles: dict[str, ServerHandle] = {}
    server_status: list[dict[str, Any]] = []
    for name, fds in groups.items():
        sspec = pool.registry.servers[name]
        entry: dict[str, Any] = {"name": name, "language": LANGUAGE_OF_SERVER.get(name, name), "files": len(fds)}
        try:
            say(f"starting {name} ...")
            h = pool.get(sspec, head_root)
            h.sync_from_disk(
                [
                    (os.path.normpath(str(head_root / (f.new_path or f.old_path or ""))), 3 if f.status == "D" else 1 if f.status == "A" else 2)
                    for f in fds
                ]
            )
            samples = [os.path.normpath(str(head_root / f.new_path)) for f in fds if f.new_path]
            samples = [s for s in samples if os.path.exists(s)]
            h.warm_up(samples)
            handles[name] = h
            entry.update(
                status="ok" if h.ready else "degraded",
                reason=h.ready_reason or None,
                version=h.version,
                server_name=(h.client.server_info or {}).get("name"),
                init_seconds=round(h.init_seconds, 2),
                ready_seconds=round(h.ready_seconds or 0.0, 2),
            )
            entry["project_notes"] = project_notes(sspec, head_root)
            say(f"{name} {'ready' if h.ready else 'NOT ready'} ({entry['ready_seconds']}s)")
        except ServerUnavailable as e:
            entry.update(status="unavailable", reason=e.reason)
            say(f"{name} unavailable: {e.reason}")
        server_status.append(entry)

    extractor = C.ChangeExtractor(spec, head_root, timeout=opt.request_timeout)
    file_changes: list[C.FileChanges] = []
    for name, fds in groups.items():
        h = handles.get(name)
        for fd in fds:
            fc = extractor.extract(fd, h, language_of(fd.path))
            if h is None:
                fc.degraded.append(f"{name} unavailable; only line-level changes reported")
            file_changes.append(fc)
    for fd in unsupported:
        fc = C.FileChanges(file=fd, server=None, language=language_of(fd.path))
        if fd.binary:
            fc.degraded.append("binary file")
        elif fc.language is None:
            fc.degraded.append("language not supported")
        file_changes.append(fc)
    say(f"classified {sum(len(fc.changes) for fc in file_changes)} changed entities")

    changed_rel = {p for fd in spec.files for p in (fd.old_path, fd.new_path) if p}
    base_handles: dict[str, ServerHandle | None] = {}
    base_root_holder: dict[str, Path] = {}

    def base_handle(sspec: ServerSpec) -> ServerHandle | None:
        if sspec.name in base_handles:
            return base_handles[sspec.name]
        try:
            if "root" not in base_root_holder:
                say(f"creating worktree for base {spec.base[:10]} (removed entities need base-side callers)")
                base_root_holder["root"] = worktree_for(spec.repo, spec.base)
            bh = pool.get(sspec, base_root_holder["root"])
        except (ServerUnavailable, gitdiff.GitError) as e:
            log.warning("base server unavailable: %s", e)
            bh = None
        base_handles[sspec.name] = bh
        return bh

    trav = Traversal(head_root, base_handle, opt, changed_rel)
    trav.head_handle_for = lambda bh: handles.get(bh.name)  # type: ignore[assignment]
    seeds = []
    for fc in file_changes:
        h = handles.get(fc.server or "")
        if h is None:
            continue
        if fc.head_index is not None and fc.file.new_path:
            trav.seed_index(h, os.path.normpath(str(head_root / fc.file.new_path)), fc.head_index)
        for ch in fc.changes:
            if ch.change == C.COSMETIC:
                n = trav.add_seed(h, ch)
                n.limit = 0
                continue
            seeds.append((trav.add_seed(h, ch), h))
    say(f"tracing callers of {len(seeds)} seeds (node budget {opt.max_nodes}) ...")
    if len(seeds) > opt.max_nodes:
        trav.traversal_skipped = True
        msg = (
            f"changed-entity count ({len(seeds)}) exceeds --max-nodes ({opt.max_nodes}); "
            "impact traversal was not run — increase --max-nodes or narrow the diff"
        )
        trav.warnings.insert(0, msg)
        say(f"WARNING: {msg}")
    else:
        trav.run(seeds, on_progress=say)
    trav.check_breakage(handles)
    report = build_report(
        spec=spec,
        head_root=head_root,
        file_changes=file_changes,
        trav=trav,
        servers=server_status,
        options=opt,
        seconds=time.monotonic() - t_start,
    )
    report["architecture"] = architecture_of(spec, head_root, report)
    if own_pool:
        pool.shutdown()
    imp = report["summary"]["impacted"]
    if report["summary"].get("impact_analyzed") is False:
        say(f"done in {report['timing']['total_seconds']}s: impact not analyzed (see warnings)")
    else:
        say(f"done in {report['timing']['total_seconds']}s: {imp} impacted entities")
    return report


def architecture_of(spec: gitdiff.DiffSpec, head_root: Path, report: dict[str, Any]) -> dict[str, Any] | None:
    from ..repo_tree import list_repo_files
    from .architecture import analyze_architecture

    def read_head(rel: str) -> str | None:
        try:
            return (head_root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    try:
        return analyze_architecture(
            repo_root=spec.repo,
            head_root=head_root,
            files=report["files"],
            nodes=report["nodes"],
            edges=report["edges"],
            read_base=lambda rel: gitdiff.read_base(spec.repo, spec.base, rel),
            read_head=read_head,
            repo_files=list_repo_files(head_root),
        )
    except Exception as e:  # noqa: BLE001 - architecture evidence is optional; never fail the impact report
        log.warning("architecture analysis failed: %s", e)
        report["warnings"].append(f"architecture analysis failed: {e}")
        return None


def project_notes(sspec: ServerSpec, root: Path) -> list[str]:
    from ..lsp.registry import ts_project_roots

    notes = []
    for need in sspec.needs:
        alts = need.split("|")
        found = any((root / a).exists() or (root / "build" / a).exists() for a in alts)
        if not found and sspec.name == "typescript":
            found = any((pr / a).exists() for pr in ts_project_roots(root) for a in alts if a.endswith(".json"))
        if not found:
            notes.append(f"missing {' or '.join(alts)} at the repository root; results may be incomplete")
    return notes
