"""Map diff hunks to entities and classify each change.

Head-side entities come from the head language server. Base-side entities come
from the same server with the base text temporarily opened as an overlay at the
same URI, so both sides are parsed and typed by the same compiler front end.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from ..git.diff import DiffSpec, FileDiff, read_base, read_head, to_native
from ..lsp.client import LspError, norm_path
from ..lsp.pool import ServerHandle
from .symbols import CALLABLE, Entity, SymbolIndex, flatten

# change classes, ordered by severity
SIGNATURE = "signature"
REMOVED = "removed"
RENAMED = "renamed"
BODY = "body"
VALUE = "value"  # data entity (field/const/var) whose declaration changed
TYPE = "type"  # class/interface/struct changed outside its members
ADDED = "added"
COSMETIC = "cosmetic"
MODULE = "module"  # lines outside any entity (imports, top-level statements)

SEED_WEIGHT = {
    SIGNATURE: 1.0,
    REMOVED: 1.0,
    RENAMED: 1.0,
    VALUE: 0.9,
    TYPE: 0.8,
    BODY: 0.6,
    ADDED: 0.3,
    MODULE: 0.3,
    COSMETIC: 0.0,
}


@dataclass
class EntityChange:
    change: str
    head: Entity | None
    base: Entity | None
    file: FileDiff
    lines_changed: int = 0
    sig_before: str | None = None
    sig_after: str | None = None
    renamed_from: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def entity(self) -> Entity:
        e = self.head or self.base
        assert e is not None
        return e

    @property
    def weight(self) -> float:
        return SEED_WEIGHT[self.change]


@dataclass
class FileChanges:
    file: FileDiff
    server: str | None
    language: str | None
    changes: list[EntityChange] = field(default_factory=list)
    module_lines: list[int] = field(default_factory=list)  # 1-based head lines outside entities
    degraded: list[str] = field(default_factory=list)
    head_index: SymbolIndex | None = None
    base_index: SymbolIndex | None = None


# ----------------------------------------------------------------------------- text helpers

_PUNCT_WS = re.compile(r"\s*([^\w\s])\s*")
_WS = re.compile(r"\s+")


def normalize_code(text: str, indentation_sensitive: bool) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    if indentation_sensitive:
        if not lines:
            return ""
        indent = min(len(ln) - len(ln.lstrip()) for ln in lines)
        return "\n".join(_PUNCT_WS.sub(r"\1", ln[indent:]) for ln in lines)
    joined = " ".join(ln.strip() for ln in lines)
    joined = _WS.sub(" ", joined)
    return _PUNCT_WS.sub(r"\1", joined).strip()


def hover_signature(hover: Any) -> str:
    """Signature text from a hover: fenced code blocks / MarkedString code, whitespace-normalised."""
    if not hover:
        return ""
    contents = hover.get("contents") if isinstance(hover, dict) else hover
    blocks: list[str] = []
    plain: list[str] = []

    def take(item: Any) -> None:
        if isinstance(item, str):
            plain.append(item)
        elif isinstance(item, dict):
            if "language" in item:
                blocks.append(item.get("value", ""))
            else:
                plain.append(item.get("value", ""))

    if isinstance(contents, list):
        for it in contents:
            take(it)
    else:
        take(contents)
    for text in plain:
        blocks.extend(m.group(1) for m in re.finditer(r"```[\w+-]*\n(.*?)```", text, re.DOTALL))
    if not blocks and plain:
        blocks = [plain[0].splitlines()[0] if plain[0] else ""]
    sig = _WS.sub(" ", "\n".join(blocks)).strip()
    # servers wrap long parameter lists differently; "( x: int, y: int )" == "(x: int, y: int)"
    return re.sub(r"([(\[<])\s+|\s+([)\]>,])", lambda m: m.group(1) or m.group(2), sig)


def slice_lines(text: str, start: int, end: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[start : end + 1])


def _header_only(text: str, single_line: bool) -> str:
    """Drop a same-line body so a one-line function is compared by its declaration."""
    if not single_line or not text:
        return text
    line = text.splitlines()[0]
    if "{" in line:
        return line.split("{", 1)[0]
    # Python one-liner: `def f(x) -> int: return x`
    m = re.match(r"^(\s*(?:async\s+)?def\b.*\S)\s*:", line)
    if m and m.end() < len(line):
        return m.group(1)
    return line


# ----------------------------------------------------------------------------- matching


def _family(kind: int) -> str:
    if kind in CALLABLE:
        return "callable"
    return str(kind)


def match_entities(base: list[Entity], head: list[Entity]) -> list[tuple[Entity | None, Entity | None]]:
    """Pair base and head entities by (qualname, kind family), in source order for overloads."""
    groups_b: dict[tuple[str, str], list[Entity]] = {}
    groups_h: dict[tuple[str, str], list[Entity]] = {}
    for e in base:
        groups_b.setdefault((e.qualname, _family(e.kind)), []).append(e)
    for e in head:
        groups_h.setdefault((e.qualname, _family(e.kind)), []).append(e)
    pairs: list[tuple[Entity | None, Entity | None]] = []
    for key in list(groups_b.keys()) + [k for k in groups_h if k not in groups_b]:
        bs = sorted(groups_b.get(key, []), key=lambda e: e.start_line)
        hs = sorted(groups_h.get(key, []), key=lambda e: e.start_line)
        if len(bs) == len(hs):
            pairs.extend(zip(bs, hs))
            continue
        # overloads added/removed: pair by identical detail first, then by order
        used: set[int] = set()
        rest_b = []
        for b in bs:
            j = next((i for i, h in enumerate(hs) if i not in used and b.detail and h.detail == b.detail), None)
            if j is None:
                rest_b.append(b)
            else:
                used.add(j)
                pairs.append((b, hs[j]))
        rest_h = [h for i, h in enumerate(hs) if i not in used]
        for b, h in zip(rest_b, rest_h):
            pairs.append((b, h))
        for b in rest_b[len(rest_h) :]:
            pairs.append((b, None))
        for h in rest_h[len(rest_b) :]:
            pairs.append((None, h))
    return pairs


# ----------------------------------------------------------------------------- extraction


class ChangeExtractor:
    def __init__(self, spec: DiffSpec, head_root, timeout: float = 60.0):
        self.spec = spec
        self.head_root = head_root
        self.timeout = timeout

    def extract(self, fd: FileDiff, handle: ServerHandle | None, language: str | None) -> FileChanges:
        fc = FileChanges(file=fd, server=handle.name if handle else None, language=language)
        head_text = read_head(self.spec.repo, self.spec.head, fd.new_path) if fd.new_path else None
        base_text = read_base(self.spec.repo, self.spec.base, fd.old_path) if fd.old_path else None
        if handle is None:
            return fc
        indent_sensitive = language == "python"
        head_path = to_native(self.head_root, fd.new_path) if fd.new_path else None
        base_path = to_native(self.head_root, fd.old_path) if fd.old_path else None

        head_ents: list[Entity] = []
        base_ents: list[Entity] = []
        try:
            with handle.lock:
                if head_path and head_text is not None:
                    handle.ensure_open(head_path, head_text if self.spec.head is not None else None)
                    head_ents = flatten(handle.client.document_symbols(head_path, self.timeout), norm_path(head_path))
                if base_path and base_text is not None:
                    base_ents, sigs_b = self._base_overlay(handle, base_path, base_text, head_path, head_text, fd)
                else:
                    sigs_b = {}
        except LspError as e:
            fc.degraded.append(f"documentSymbol failed: {e}")
            return fc

        fc.head_index = SymbolIndex(head_ents)
        fc.base_index = SymbolIndex(base_ents)
        head_touched: dict[int, int] = {}  # id(entity) -> changed lines
        base_touched: dict[int, int] = {}
        id2h = {id(e): e for e in fc.head_index.entities}
        id2b = {id(e): e for e in fc.base_index.entities}
        for line in sorted(fd.changed_new_lines()):
            ent = fc.head_index.innermost(line)
            if ent is None:
                fc.module_lines.append(line + 1)
            else:
                head_touched[id(ent)] = head_touched.get(id(ent), 0) + 1
        for line in sorted(fd.changed_old_lines()):
            ent = fc.base_index.innermost(line)
            if ent is not None:
                base_touched[id(ent)] = base_touched.get(id(ent), 0) + 1
        # pure deletions inside a head entity (old lines may fall outside base entities when
        # the base overlay failed): attribute to the head entity spanning the deletion point
        for h in fd.hunks:
            if h.new_count == 0 and fd.new_path:
                ent = fc.head_index.innermost(h.new_anchor())
                if ent is not None and ent.contains_line(h.new_anchor() + 1):
                    head_touched.setdefault(id(ent), 0)

        pairs = match_entities(fc.base_index.entities, fc.head_index.entities)
        pair_of_head = {id(h): b for b, h in pairs if h is not None}
        pair_of_base = {id(b): h for b, h in pairs if b is not None}

        touched: list[tuple[Entity | None, Entity | None, int]] = []
        seen: set[tuple[int, int]] = set()
        for hid, n in head_touched.items():
            h = id2h[hid]
            b = pair_of_head.get(hid)
            n += base_touched.get(id(b), 0) if b is not None else 0
            key = (id(b) if b is not None else 0, hid)
            if key not in seen:
                seen.add(key)
                touched.append((b, h, n))
        for bid, n in base_touched.items():
            b = id2b[bid]
            h = pair_of_base.get(bid)
            key = (bid, id(h) if h is not None else 0)
            if key in seen:
                continue
            seen.add(key)
            touched.append((b, h, n))

        # parents of touched children are not changes themselves unless their own lines changed
        changes: list[EntityChange] = []
        for b, h, n in touched:
            changes.append(self._classify(fd, handle, b, h, n, base_text, head_text, head_path, sigs_b, indent_sensitive))
        changes = self._detect_renames(changes, base_text, head_text, indent_sensitive)
        changes = self._drop_container_noise(changes)
        fc.changes = sorted(changes, key=lambda c: (c.entity.start_line, c.entity.name))
        return fc

    # ------------------------------------------------------------------ base overlay
    def _base_overlay(
        self,
        handle: ServerHandle,
        base_path: str,
        base_text: str,
        head_path: str | None,
        head_text: str | None,
        fd: FileDiff,
    ) -> tuple[list[Entity], dict[tuple[str, int], str]]:
        """Open base text at the same URI, read symbols + signatures of touched entities, restore."""
        client = handle.client
        same_uri = head_path is not None and norm_path(head_path) == norm_path(base_path)
        sigs: dict[tuple[str, int], str] = {}
        handle.ensure_open(base_path, base_text)
        try:
            # jdtls (and slow indexers) need a quiet period after didChange before symbols settle
            settle = 8.0 if handle.name == "jdtls" else 2.0
            client.wait_quiet(timeout=settle, quiet=0.6)
            ents = flatten(client.document_symbols(base_path, self.timeout), norm_path(base_path))
            if not ents and handle.name == "jdtls":
                client.wait_quiet(timeout=12.0, quiet=0.8)
                ents = flatten(client.document_symbols(base_path, self.timeout), norm_path(base_path))
            idx = SymbolIndex(ents)
            touched_lines = fd.changed_old_lines()
            for e in idx.entities:
                if any(e.contains_line(ln) for ln in touched_lines) or fd.status == "D":
                    try:
                        sigs[(e.qualname, e.start_line)] = hover_signature(
                            client.hover(base_path, e.sel_line, e.sel_char, self.timeout)
                        )
                    except LspError:
                        pass
            return idx.entities, sigs
        finally:
            if same_uri and head_text is not None:
                handle.ensure_open(head_path, head_text)  # type: ignore[arg-type]
                if handle.name == "jdtls":
                    client.wait_quiet(timeout=8.0, quiet=0.6)
            else:
                client.close_document(base_path)
                if head_path and head_text is not None and self.spec.head is None:
                    handle.ensure_open(head_path, head_text)

    # ------------------------------------------------------------------ classification
    def _classify(
        self,
        fd: FileDiff,
        handle: ServerHandle,
        b: Entity | None,
        h: Entity | None,
        n: int,
        base_text: str | None,
        head_text: str | None,
        head_path: str | None,
        sigs_b: dict[tuple[str, int], str],
        indent_sensitive: bool,
    ) -> EntityChange:
        if h is None:
            assert b is not None
            return EntityChange(REMOVED, None, b, fd, n, sig_before=sigs_b.get((b.qualname, b.start_line)))
        if b is None:
            return EntityChange(ADDED, h, None, fd, n, sig_after=self._sig(handle, head_path, h))
        before = normalize_code(slice_lines(base_text or "", b.start_line, b.end_line), indent_sensitive)
        after = normalize_code(slice_lines(head_text or "", h.start_line, h.end_line), indent_sensitive)
        if before == after:
            return EntityChange(COSMETIC, h, b, fd, n)
        sig_b = sigs_b.get((b.qualname, b.start_line))
        sig_h = self._sig(handle, head_path, h)
        ch = EntityChange(BODY, h, b, fd, n, sig_before=sig_b, sig_after=sig_h)
        if h.is_type:
            # innermost() only attributes lines to a type when they are outside its members
            ch.change = TYPE
        elif not h.is_callable:
            ch.change = VALUE
        if sig_b is not None and sig_h is not None and sig_b != sig_h:
            ch.change = SIGNATURE if (h.is_callable or h.is_type) else VALUE
        elif h.is_callable and self._header_changed(h, b, base_text, head_text):
            # jdtls hover often keeps the head signature after a base overlay; trust the text
            ch.change = SIGNATURE
            ch.notes.append("declaration header text changed")
        elif sig_b is None or sig_h is None:
            ch.notes.append("signature not compared (hover unavailable)")
        return ch

    def _sig(self, handle: ServerHandle, path: str | None, e: Entity) -> str | None:
        if not path:
            return None
        try:
            return hover_signature(handle.client.hover(path, e.sel_line, e.sel_char, self.timeout))
        except LspError:
            return None

    @staticmethod
    def _header_changed(h: Entity, b: Entity, base_text: str | None, head_text: str | None) -> bool:
        # One-line declarations (Java interface methods) use the whole span; longer
        # callables compare from the start of the entity through the name selection.
        # A body on that same line (`func F() int { return 1 }`) is not part of the header.
        b_end = b.end_line if b.end_line == b.start_line else max(b.sel_line, b.start_line)
        h_end = h.end_line if h.end_line == h.start_line else max(h.sel_line, h.start_line)
        hb = normalize_code(_header_only(slice_lines(base_text or "", b.start_line, b_end), b.start_line == b_end), False)
        hh = normalize_code(_header_only(slice_lines(head_text or "", h.start_line, h_end), h.start_line == h_end), False)
        return hb != hh

    def _detect_renames(
        self, changes: list[EntityChange], base_text: str | None, head_text: str | None, ind: bool
    ) -> list[EntityChange]:
        removed = [c for c in changes if c.change == REMOVED]
        added = [c for c in changes if c.change == ADDED]
        if not removed or not added:
            return changes
        used: set[int] = set()
        for r in removed:
            rb = r.base
            assert rb is not None
            body_r = normalize_code(slice_lines(base_text or "", rb.start_line, rb.end_line), ind).replace(rb.name, "\0")
            best, best_ratio = None, 0.0
            for i, a in enumerate(added):
                ah = a.head
                assert ah is not None
                if i in used or _family(ah.kind) != _family(rb.kind) or ah.container != rb.container:
                    continue
                body_a = normalize_code(slice_lines(head_text or "", ah.start_line, ah.end_line), ind).replace(ah.name, "\0")
                ratio = difflib.SequenceMatcher(None, body_r, body_a, autojunk=False).ratio()
                if ratio > best_ratio:
                    best, best_ratio = i, ratio
            if best is not None and best_ratio >= 0.85:
                used.add(best)
                a = added[best]
                a.change = RENAMED
                a.base = rb
                a.renamed_from = rb.qualname
                a.sig_before = r.sig_before
                a.lines_changed += r.lines_changed
                r.change = "_drop"
        return [c for c in changes if c.change != "_drop"]

    @staticmethod
    def _drop_container_noise(changes: list[EntityChange]) -> list[EntityChange]:
        """A class whose only modifications are inside changed members is not itself a change."""
        out = []
        member_changed = {id(c.head) for c in changes if c.head is not None}
        for c in changes:
            if c.change == TYPE and c.head is not None and c.lines_changed == 0:
                if any(id(ch) in member_changed for ch in c.head.children):
                    continue
            out.append(c)
        return out
