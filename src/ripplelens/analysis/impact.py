"""Blast-radius traversal over compiler-backed language servers.

Seeds are changed entities. Edges come only from the language server:

  call        callHierarchy/incomingCalls
  ref         textDocument/references that are not call sites (value use, field/type use)
  dispatch    callers of a method this method overrides/implements (may dispatch here)
  implements  implementations/overrides of a changed abstract or interface method

score(child) = score(parent) * DECAY * EDGE_RISK[kind]; seed score = change weight.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..lsp.client import LspError, LspTimeout, norm_path, true_case, uri_to_path
from ..lsp.pool import ServerHandle
from . import changes as C
from .symbols import CLASS, INTERFACE, METHOD, STRUCT, Entity, SymbolIndex, flatten

log = logging.getLogger("ripplelens.impact")

DECAY = 0.6
EDGE_RISK = {"call": 1.0, "ref": 0.8, "dispatch": 0.7, "implements": 1.0}

_IMPORT_LINE = re.compile(r"^\s*(import\b|from\s+\S+\s+import\b|#\s*include\b|using\b|package\b|export\s+\{|export\s+\*)")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_KEYWORDS = {"class", "extends", "implements", "public", "private", "protected", "virtual", "final", "abstract",
             "struct", "interface", "export", "default", "metaclass", "object", "Generic", "Protocol", "typing"}

BLIND_SPOTS = {
    "go": ["calls through reflect", "functions stored in maps/struct fields and invoked later",
           "cgo and go:linkname"],
    "typescript": ["calls through values typed `any`/`unknown`", "string-keyed dynamic property access",
                   "dynamic import() with computed specifiers"],
    "javascript": ["untyped JavaScript: calls on values whose type is not inferable", "dynamic property access"],
    "python": ["getattr/setattr and other dynamic attribute access", "untyped code where the receiver type is unknown",
               "decorator or metaclass indirection", "monkey patching"],
    "java": ["reflection (Class.forName, Method.invoke)", "dependency-injection wiring resolved at runtime",
             "calls from non-Java resources (XML, templates)"],
    "c": ["calls through function pointers", "macros expanding to calls", "files without a compile command"],
    "cpp": ["calls through function pointers and std::function", "template instantiations not seen by the index",
            "files without a compile command"],
}


@dataclass
class Options:
    max_depth: int = 3
    body_depth: int = 2
    max_nodes: int = 400
    min_score: float = 0.05
    hub_threshold: int = 25
    hub_keep: int = 8
    request_timeout: float = 60.0
    value_refs: bool = True
    dispatch: bool = True
    diagnostics: bool = True
    base_server: bool = True


@dataclass
class Node:
    id: str
    path: str  # repo-relative, forward slashes
    abs_path: str
    name: str
    qualname: str
    kind: str
    start_line: int
    end_line: int
    sel_line: int
    sel_char: int
    server: str
    depth: int = 0
    score: float = 0.0
    limit: int = 0
    change: str | None = None
    change_ref: C.EntityChange | None = field(default=None, repr=False)
    seeds: set[str] = field(default_factory=set)
    parent: str | None = None
    via: str | None = None
    confidence: str = "precise"
    reasons: list[str] = field(default_factory=list)
    breaking: list[dict[str, Any]] = field(default_factory=list)
    truncated: dict[str, Any] = field(default_factory=dict)
    expanded: bool = False
    on_base: bool = False  # entity lives on the base revision only (removed)
    entity: Entity | None = field(default=None, repr=False)
    base_query: tuple[str, Entity] | None = field(default=None, repr=False)  # renamed: (old rel path, old entity)

    def degrade(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)
        self.confidence = "degraded"


@dataclass
class Edge:
    source: str
    target: str
    kind: str
    sites: list[int] = field(default_factory=list)  # 1-based lines in the target's file


class Traversal:
    def __init__(self, head_root: Path, base_root_fn, options: Options, changed_rel: set[str]):
        self.head_root = head_root
        self.base_root_fn = base_root_fn  # (spec) -> ServerHandle | None   lazily started base server
        self.opt = options
        self.changed_rel = changed_rel
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}
        self.truncation: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.traversal_skipped = False
        self._indexes: dict[tuple[str, str], SymbolIndex] = {}
        self._texts: dict[str, list[str]] = {}
        self.stats = {"requests": 0, "request_seconds": 0.0, "timeouts": 0}

    # ------------------------------------------------------------------ helpers
    def rel(self, path: str, root: Path | None = None) -> str:
        root = root or self.head_root
        try:
            return Path(os.path.relpath(true_case(path), true_case(root))).as_posix()
        except ValueError:
            return Path(path).as_posix()

    def node_id(self, rel: str, e: Entity) -> str:
        return f"{rel}#{e.qualname}@{e.start_line + 1}"

    def index(self, handle: ServerHandle, path: str) -> SymbolIndex | None:
        key = (handle.name + ":" + norm_path(handle.root), norm_path(path))
        if key in self._indexes:
            return self._indexes[key]
        try:
            with handle.lock:
                handle.ensure_open(path)
                syms = self._call(handle, "documentSymbol", handle.client.document_symbols, path, self.opt.request_timeout)
        except (LspError, OSError) as e:
            self.warnings.append(f"{handle.name}: documentSymbol failed for {self.rel(path)}: {e}")
            return None
        idx = SymbolIndex(flatten(syms, norm_path(path)))
        self._indexes[key] = idx
        return idx

    def seed_index(self, handle: ServerHandle, path: str, idx: SymbolIndex) -> None:
        self._indexes[(handle.name + ":" + norm_path(handle.root), norm_path(path))] = idx

    def line_text(self, path: str, line: int) -> str:
        key = norm_path(path)
        if key not in self._texts:
            try:
                self._texts[key] = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                self._texts[key] = []
        lines = self._texts[key]
        return lines[line] if 0 <= line < len(lines) else ""

    def _call(self, handle: ServerHandle, label: str, fn, *args):
        t0 = time.monotonic()
        self.stats["requests"] += 1
        try:
            return fn(*args)
        except LspTimeout:
            self.stats["timeouts"] += 1
            raise
        finally:
            self.stats["request_seconds"] += time.monotonic() - t0

    def make_node(self, handle: ServerHandle, path: str, e: Entity, root: Path | None = None) -> Node:
        rel = self.rel(path, root)
        nid = self.node_id(rel, e)
        n = self.nodes.get(nid)
        if n is None:
            n = Node(
                id=nid,
                path=rel,
                abs_path=path,
                name=e.name,
                qualname=e.qualname,
                kind=e.kind_name,
                start_line=e.start_line,
                end_line=e.end_line,
                sel_line=e.sel_line,
                sel_char=e.sel_char,
                server=handle.name,
                entity=e,
            )
            self.nodes[nid] = n
        return n

    def module_node(self, handle: ServerHandle, path: str) -> Node:
        rel = self.rel(path)
        nid = f"{rel}#<module>"
        n = self.nodes.get(nid)
        if n is None:
            n = Node(nid, rel, path, "<module>", "<module>", "module", 0, 0, 0, 0, handle.name)
            self.nodes[nid] = n
        return n

    def add_edge(self, source: Node, target: Node, kind: str, sites: list[int]) -> None:
        key = (source.id, target.id, kind)
        e = self.edges.get(key)
        if e is None:
            self.edges[key] = Edge(source.id, target.id, kind, sorted(set(sites)))
        else:
            e.sites = sorted(set(e.sites) | set(sites))

    # ------------------------------------------------------------------ seeding
    def add_seed(self, handle: ServerHandle, ch: C.EntityChange, base_handle: ServerHandle | None = None) -> Node:
        e = ch.entity
        on_base = ch.head is None
        if on_base:
            path = os.path.normpath(str(self.head_root / (ch.file.old_path or "")))
        else:
            path = os.path.normpath(str(self.head_root / (ch.file.new_path or "")))
        n = self.make_node(handle, path, e)
        n.change = ch.change
        n.change_ref = ch
        n.on_base = on_base
        # Renames and signature changes: callers / implementors of the *old* declaration
        # are only visible on the base revision (jdtls drops them once the head signature
        # no longer matches). Query that side and map hits back to head.
        if ch.change in (C.RENAMED, C.SIGNATURE) and ch.base is not None:
            n.base_query = (ch.file.old_path or ch.file.path, ch.base)
        n.seeds.add(n.id)
        n.score = max(n.score, ch.weight)
        if ch.change in (C.SIGNATURE, C.REMOVED, C.RENAMED, C.VALUE, C.TYPE):
            n.limit = max(n.limit, self.opt.max_depth)
        elif ch.change in (C.BODY, C.ADDED, C.MODULE):
            n.limit = max(n.limit, min(self.opt.body_depth, self.opt.max_depth))
        else:
            n.limit = max(n.limit, 0)
        if not handle.ready:
            n.degrade(f"{handle.name} not fully ready: {handle.ready_reason}")
        return n

    def budget_nodes(self) -> int:
        """Nodes that count toward max_nodes (whitespace-only seeds are excluded)."""
        return sum(1 for n in self.nodes.values() if n.change != C.COSMETIC)

    # ------------------------------------------------------------------ traversal
    def run(self, seeds: list[tuple[Node, ServerHandle]], on_progress: Callable[[str], None] | None = None) -> None:
        handles = {n.id: h for n, h in seeds}
        queue: deque[str] = deque(n.id for n, _ in sorted(seeds, key=lambda s: -s[0].score))
        seen_req = 0
        while queue:
            nid = queue.popleft()
            node = self.nodes[nid]
            if node.expanded:
                continue
            node.expanded = True
            if node.depth >= node.limit:
                continue
            if node.score * DECAY < self.opt.min_score:
                self.truncation.append({"at": node.id, "reason": "min_score", "score": round(node.score, 3)})
                continue
            if self.budget_nodes() >= self.opt.max_nodes:
                self.truncation.append({"at": node.id, "reason": "budget", "max_nodes": self.opt.max_nodes})
                continue
            handle = handles[nid]
            for child, kind, h2 in self.expand(node, handle):
                score = node.score * DECAY * EDGE_RISK[kind]
                new = child.parent is None and not child.seeds
                if new or (child.change is None and score > child.score):
                    if child.change is None:
                        child.depth = node.depth + 1
                        child.score = score
                        child.parent = node.id
                        child.via = kind
                        child.limit = node.limit
                        if kind == "dispatch" and child.confidence == "precise":
                            child.confidence = "possible"
                            child.reasons.append("reached through dynamic dispatch; may not call the changed override")
                child.seeds |= node.seeds
                handles.setdefault(child.id, h2)
                if child.change is None and not child.expanded:
                    queue.append(child.id)
            if on_progress and self.stats["requests"] - seen_req >= 25:
                seen_req = self.stats["requests"]
                impacted = sum(1 for n in self.nodes.values() if n.change is None)
                on_progress(f"traversal: {self.stats['requests']} LSP requests, {impacted} impacted entities")

    def expand(self, node: Node, handle: ServerHandle) -> list[tuple[Node, str, ServerHandle]]:
        out: list[tuple[Node, str, ServerHandle]] = []
        entity = node.entity
        if entity is None:
            return out
        query_handle, query_path, root = handle, node.abs_path, self.head_root
        pos = (node.sel_line, node.sel_char)
        if node.on_base or node.base_query is not None:
            # removed or renamed: whoever used the old entity is found on the base revision
            rel, qent = node.base_query or (node.path, entity)
            bh = self.base_root_fn(handle.spec) if self.opt.base_server else None
            if bh is None:
                node.degrade("callers of the old entity not queried on the base revision (base server unavailable)")
                return out
            query_handle, root = bh, bh.root
            query_path = os.path.normpath(str(bh.root / rel))
            pos = (qent.sel_line, qent.sel_char)
            entity = qent
            try:
                bh.warm_up([query_path])
            except Exception as ex:
                node.degrade(f"base server warm-up failed: {ex}")
                return out
        is_seed = node.change is not None
        call_sites: set[tuple[str, int, int]] = set()
        if entity.is_callable:
            out += self._incoming(node, query_handle, query_path, root, call_sites, pos)
            if is_seed and self.opt.value_refs:
                out += self._refs(node, query_handle, query_path, root, call_sites, "ref", pos)
            if is_seed and self.opt.dispatch and query_handle is handle and node.entity is not None:
                out += self._dispatch(node, handle, node.entity)
            if is_seed and node.change in (C.SIGNATURE, C.REMOVED, C.RENAMED) and entity.kind == METHOD:
                out += self._implementations(node, query_handle, query_path, root, pos)
        else:
            out += self._refs(node, query_handle, query_path, root, set(), "ref", pos)
        return out

    # ------------------------------------------------------------------ edge sources
    def _map_location(
        self, handle: ServerHandle, root: Path, uri: str, line: int
    ) -> tuple[Node | None, ServerHandle]:
        path = uri_to_path(uri)
        if handle.root != self.head_root:
            # base revision: translate to the head file and entity
            rel = self.rel(path, root)
            head_path = os.path.normpath(str(self.head_root / rel))
            if not os.path.exists(head_path):
                return None, handle
            base_idx = self.index(handle, path)
            ent_b = base_idx.enclosing_for_impact(line) if base_idx else None
            head_h = self.head_handle_for(handle)
            if head_h is None:
                return None, handle
            head_idx = self.index(head_h, head_path)
            if not head_idx:
                return None, handle
            if rel not in self.changed_rel:
                ent_h = head_idx.enclosing_for_impact(line)
            else:
                cands = head_idx.by_qualname(ent_b.qualname) if ent_b else []
                ent_h = cands[0] if cands else None
            if ent_h is None:
                return None, head_h
            n = self.make_node(head_h, head_path, ent_h)
            if "caller found on the base revision" not in n.reasons:
                n.reasons.append("caller found on the base revision")
            return n, head_h
        idx = self.index(handle, path)
        if idx is None:
            return None, handle
        ent = idx.enclosing_for_impact(line)
        if ent is None:
            if _IMPORT_LINE.match(self.line_text(path, line)):
                return None, handle
            return self.module_node(handle, path), handle
        if not path_within(path, self.head_root):
            return None, handle
        return self.make_node(handle, path, ent), handle

    head_handle_for = None  # set by the pipeline: (base ServerHandle) -> head ServerHandle

    def _hub_fold(self, node: Node, items: list[Any], label: str, key) -> list[Any]:
        if len(items) <= self.opt.hub_threshold:
            return items
        node_dir = Path(node.path).parent.as_posix()
        items = sorted(items, key=lambda it: (Path(key(it)).parent.as_posix() != node_dir, key(it)))
        kept = items[: self.opt.hub_keep]
        node.truncated[label] = {"total": len(items), "shown": len(kept)}
        self.truncation.append({"at": node.id, "reason": "hub", "edge": label, "total": len(items), "shown": len(kept)})
        return kept

    def _incoming(
        self,
        node: Node,
        handle: ServerHandle,
        path: str,
        root: Path,
        call_sites: set[tuple[str, int, int]],
        pos: tuple[int, int] | None = None,
    ) -> list[tuple[Node, str, ServerHandle]]:
        out = []
        line, char = pos or (node.sel_line, node.sel_char)
        try:
            with handle.lock:
                handle.ensure_open(path)
                items = self._call(handle, "prepareCallHierarchy", handle.client.prepare_call_hierarchy,
                                   path, line, char, self.opt.request_timeout)
                if not items:
                    return out
                item = min(items, key=lambda it: abs(it["selectionRange"]["start"]["line"] - line))
                calls = self._call(handle, "incomingCalls", handle.client.incoming_calls, item, self.opt.request_timeout)
        except LspError as e:
            node.degrade(f"incoming calls failed: {e}")
            return out
        for c in calls:
            uri = c["from"]["uri"]
            for r in c.get("fromRanges", []):
                call_sites.add((norm_path(uri_to_path(uri)), r["start"]["line"], r["start"]["character"]))
        calls = self._hub_fold(node, calls, "callers", lambda c: uri_to_path(c["from"]["uri"]))
        for c in calls:
            fr = c["from"]
            child, h2 = self._map_location(handle, root, fr["uri"], fr["selectionRange"]["start"]["line"])
            if child is None or child.id == node.id:
                continue
            sites = [r["start"]["line"] + 1 for r in c.get("fromRanges", [])]
            self.add_edge(node, child, "call", sites)
            out.append((child, "call", h2))
        return out

    def _refs(
        self,
        node: Node,
        handle: ServerHandle,
        path: str,
        root: Path,
        exclude: set[tuple[str, int, int]],
        kind: str,
        pos: tuple[int, int] | None = None,
    ) -> list[tuple[Node, str, ServerHandle]]:
        out = []
        line, char = pos or (node.sel_line, node.sel_char)
        try:
            with handle.lock:
                handle.ensure_open(path)
                refs = self._call(handle, "references", handle.client.references,
                                  path, line, char, False, self.opt.request_timeout)
        except LspError as e:
            node.degrade(f"references failed: {e}")
            return out
        decl = (norm_path(path), line)
        filtered = []
        for r in refs:
            p = norm_path(uri_to_path(r["uri"]))
            ln, ch = r["range"]["start"]["line"], r["range"]["start"]["character"]
            if (p, ln, ch) in exclude or (p, ln) == decl:
                continue
            filtered.append(r)
        grouped: dict[str, list[int]] = {}
        targets: dict[str, tuple[Node, ServerHandle]] = {}
        filtered = self._hub_fold(node, filtered, "references", lambda r: uri_to_path(r["uri"]))
        for r in filtered:
            ln = r["range"]["start"]["line"]
            child, h2 = self._map_location(handle, root, r["uri"], ln)
            if child is None or child.id == node.id:
                continue
            grouped.setdefault(child.id, []).append(ln + 1)
            targets[child.id] = (child, h2)
        for cid, (child, h2) in targets.items():
            self.add_edge(node, child, kind, grouped[cid])
            out.append((child, kind, h2))
        return out

    def _implementations(
        self, node: Node, handle: ServerHandle, path: str, root: Path, pos: tuple[int, int] | None = None
    ) -> list[tuple[Node, str, ServerHandle]]:
        out = []
        entity = node.base_query[1] if node.base_query else node.entity
        assert entity is not None
        line, char = pos or (node.sel_line, node.sel_char)
        try:
            with handle.lock:
                locs = self._call(handle, "implementation", handle.client.implementation,
                                  path, line, char, self.opt.request_timeout)
        except LspError as e:
            node.degrade(f"implementation failed: {e}")
            return out
        my_container_kind = self._container_kind(handle, path, entity)
        for loc in locs:
            if not loc.get("uri") or not loc.get("range"):
                continue
            child, h2 = self._map_location(handle, root, loc["uri"], loc["range"]["start"]["line"])
            if child is None or child.id == node.id or child.entity is None:
                continue
            # gopls answers both directions; an interface method is a super, not an implementation
            ck = self._container_kind(h2, child.abs_path, child.entity)
            if ck == INTERFACE and my_container_kind != INTERFACE:
                continue
            self.add_edge(node, child, "implements", [loc["range"]["start"]["line"] + 1])
            out.append((child, "implements", h2))
        return out

    def _container_kind(self, handle: ServerHandle, path: str, e: Entity) -> int | None:
        idx = self.index(handle, path)
        if not idx or not e.container:
            return None
        best = None
        for t in idx.entities:
            if t.is_type and t.contains_line(e.start_line) and t is not e:
                if best is None or t.span < best.span:
                    best = t
        return best.kind if best else None

    # ------------------------------------------------------------------ dispatch (overridden methods)
    def _dispatch(self, node: Node, handle: ServerHandle, entity: Entity) -> list[tuple[Node, str, ServerHandle]]:
        if entity.kind != METHOD:
            return []
        out: list[tuple[Node, str, ServerHandle]] = []
        for super_path, super_ent in self.super_methods(handle, node.abs_path, entity):
            super_node = self.make_node(handle, super_path, super_ent)
            if super_node.id == node.id:
                continue
            sites: set[tuple[str, int, int]] = set()
            for child, kind, h2 in self._incoming(super_node, handle, super_path, self.head_root, sites):
                if child.id == node.id:
                    continue
                self.add_edge(node, child, "dispatch", [s for e in [self.edges.get((super_node.id, child.id, "call"))] if e for s in e.sites])
                self.edges.pop((super_node.id, child.id, "call"), None)
                out.append((child, "dispatch", h2))
            if not super_node.seeds and super_node.parent is None and super_node.change is None:
                # the super declaration is context, not an impacted entity
                self.nodes.pop(super_node.id, None)
        return out

    def super_methods(self, handle: ServerHandle, path: str, method: Entity) -> list[tuple[str, Entity]]:
        idx = self.index(handle, path)
        if idx is None:
            return []
        cls = None
        for t in idx.entities:
            if t.kind in (CLASS, STRUCT, INTERFACE) and t.contains_line(method.start_line) and t is not method:
                if cls is None or t.span < cls.span:
                    cls = t
        found: list[tuple[str, Entity]] = []
        seen: set[str] = set()

        def add_from_type(type_path: str, type_line: int) -> None:
            tidx = self.index(handle, type_path)
            if not tidx:
                return
            tcls = None
            for t in tidx.entities:
                if t.is_type and t.contains_line(type_line):
                    if tcls is None or t.span < tcls.span:
                        tcls = t
            if tcls is None:
                return
            for m in tidx.entities:
                if m.is_callable and m.name == method.name and tcls.contains_line(m.start_line) and m is not method:
                    k = f"{norm_path(type_path)}:{m.start_line}"
                    if k not in seen:
                        seen.add(k)
                        found.append((type_path, m))

        caps = handle.client.capabilities
        if cls is not None and caps.get("typeHierarchyProvider"):
            try:
                with handle.lock:
                    items = handle.client.prepare_type_hierarchy(path, cls.sel_line, cls.sel_char, self.opt.request_timeout)
                    frontier = list(items[:1])
                    for _ in range(6):
                        nxt = []
                        for it in frontier:
                            for sup in handle.client.supertypes(it, self.opt.request_timeout):
                                nxt.append(sup)
                                add_from_type(uri_to_path(sup["uri"]), sup["selectionRange"]["start"]["line"])
                        frontier = nxt
                        if not frontier:
                            break
            except LspError:
                pass
        if not found and handle.name == "gopls":
            try:
                with handle.lock:
                    locs = handle.client.implementation(path, method.sel_line, method.sel_char, self.opt.request_timeout)
                for loc in locs:
                    p = uri_to_path(loc["uri"])
                    lidx = self.index(handle, p)
                    ent = lidx.innermost(loc["range"]["start"]["line"]) if lidx else None
                    if ent is not None and ent.is_callable and self._container_kind(handle, p, ent) == INTERFACE:
                        found.append((p, ent))
            except LspError:
                pass
        if not found and cls is not None and not caps.get("typeHierarchyProvider"):
            for type_path, type_line in self._bases_from_header(handle, path, cls):
                add_from_type(type_path, type_line)
        return found

    def _bases_from_header(self, handle: ServerHandle, path: str, cls: Entity, depth: int = 0) -> list[tuple[str, int]]:
        """Resolve base classes named in the declaration header via textDocument/definition."""
        if depth > 5:
            return []
        lines = []
        for ln in range(cls.sel_line, min(cls.end_line, cls.sel_line + 8) + 1):
            lines.append((ln, self.line_text(path, ln)))
        results: list[tuple[str, int]] = []
        stop = False
        for ln, text in lines:
            start = cls.sel_char + len(cls.name) if ln == cls.sel_line else 0
            segment = text[start:]
            cut = len(segment)
            for i, chh in enumerate(segment):
                if chh == "{" or (chh == ":" and handle.name == "pyright" and segment[:i].count("(") <= segment[:i].count(")")):
                    cut, stop = i, True
                    break
            for m in _IDENT.finditer(segment[:cut]):
                if m.group(0) in _KEYWORDS:
                    continue
                try:
                    with handle.lock:
                        locs = handle.client.definition(path, ln, start + m.start(), self.opt.request_timeout)
                except LspError:
                    continue
                for loc in locs:
                    if not loc.get("uri") or not loc.get("range"):
                        continue
                    p = uri_to_path(loc["uri"])
                    tl = loc["range"]["start"]["line"]
                    tidx = self.index(handle, p)
                    ent = tidx.innermost(tl) if tidx else None
                    if ent is not None and ent.is_type and (p, tl) not in results:
                        results.append((p, tl))
                        results.extend(self._bases_from_header(handle, p, ent, depth + 1))
            if stop:
                break
        return results

    # ------------------------------------------------------------------ breakage
    def check_breakage(self, handles: dict[str, ServerHandle]) -> None:
        if not self.opt.diagnostics:
            return
        targets: dict[str, list[tuple[Node, list[int]]]] = {}
        for e in self.edges.values():
            src = self.nodes.get(e.source)
            dst = self.nodes.get(e.target)
            if not src or not dst or src.change not in (C.SIGNATURE, C.REMOVED, C.RENAMED, C.VALUE, C.TYPE):
                continue
            if e.kind not in ("call", "ref", "implements"):
                continue
            targets.setdefault(dst.abs_path, []).append((dst, e.sites))
        for path, items in targets.items():
            h = handles.get(items[0][0].server)
            if h is None or not os.path.exists(path):
                continue
            t0 = time.monotonic()
            with h.lock:
                h.ensure_open(path)
            diags: list[dict[str, Any]] | None = None
            try:
                with h.lock:
                    diags = h.client.pull_diagnostics(path, timeout=self.opt.request_timeout)
            except LspError:
                diags = None
            if diags is None:
                diags = h.client.wait_diagnostics(path, since=0, timeout=15.0)
                h.client.wait_quiet(timeout=10.0, quiet=0.8)
                diags = h.client.diagnostics.get(norm_path(path), diags)
            if diags is None:
                for n, _ in items:
                    n.degrade("no diagnostics received for call sites")
                continue
            for n, sites in items:
                for d in diags:
                    if d.get("severity", 1) != 1:
                        continue
                    r = d["range"]
                    lo, hi = r["start"]["line"] + 1, r["end"]["line"] + 1
                    if any(lo <= s <= hi for s in sites) or (not sites and n.start_line < lo <= n.end_line + 1):
                        msg = " ".join(str(d.get("message", "")).split())[:300]
                        entry = {"line": lo, "message": msg, "source": d.get("source", h.name)}
                        if entry not in n.breaking:
                            n.breaking.append(entry)
            log.debug("diagnostics for %s in %.2fs", path, time.monotonic() - t0)


def path_within(path: str, root: Path) -> bool:
    try:
        return os.path.commonpath([norm_path(path), norm_path(root)]) == norm_path(root)
    except ValueError:
        return False
