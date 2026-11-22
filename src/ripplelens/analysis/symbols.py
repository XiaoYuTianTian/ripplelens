"""Turn LSP documentSymbol results into flat, addressable entities."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any

from ..lsp.client import uri_to_path

# LSP SymbolKind
FILE, MODULE, NAMESPACE, PACKAGE, CLASS, METHOD, PROPERTY, FIELD, CONSTRUCTOR, ENUM = range(1, 11)
INTERFACE, FUNCTION, VARIABLE, CONSTANT, STRING, NUMBER, BOOLEAN, ARRAY, OBJECT, KEY = range(11, 21)
NULL, ENUM_MEMBER, STRUCT, EVENT, OPERATOR, TYPE_PARAMETER = range(21, 27)

KIND_NAMES = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class", 6: "method", 7: "property",
    8: "field", 9: "constructor", 10: "enum", 11: "interface", 12: "function", 13: "variable",
    14: "constant", 15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object", 20: "key",
    21: "null", 22: "enum_member", 23: "struct", 24: "event", 25: "operator", 26: "type_parameter",
}

CALLABLE = {METHOD, CONSTRUCTOR, FUNCTION, OPERATOR}
TYPES = {CLASS, INTERFACE, STRUCT, ENUM, TYPE_PARAMETER}
CONTAINERS = {FILE, MODULE, NAMESPACE, PACKAGE}
DATA = {PROPERTY, FIELD, VARIABLE, CONSTANT, ENUM_MEMBER, EVENT}
# value-like kinds that some servers emit for object-literal keys etc. (ignored as entities)
NOISE = {STRING, NUMBER, BOOLEAN, ARRAY, OBJECT, KEY, NULL}


@dataclass
class Entity:
    name: str
    kind: int
    path: str
    start_line: int  # 0-based inclusive
    end_line: int  # 0-based inclusive
    sel_line: int
    sel_char: int
    container: str = ""
    detail: str = ""
    depth: int = 0
    in_callable: bool = False
    children: list[Entity] = field(default_factory=list, repr=False)

    @property
    def qualname(self) -> str:
        return f"{self.container}.{self.name}" if self.container else self.name

    @property
    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, str(self.kind))

    @property
    def is_callable(self) -> bool:
        return self.kind in CALLABLE

    @property
    def is_type(self) -> bool:
        return self.kind in TYPES

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line

    @property
    def span(self) -> int:
        return self.end_line - self.start_line

    def key(self) -> tuple[str, str, int]:
        return (self.path, self.qualname, self.kind)


def _clean_name(name: str) -> str:
    # gopls reports methods as "(T).M" or "(*T).M"; normalise to "T.M" container split later.
    # jdtls includes the parameter list in the name ("area()" / "area(double)"); strip it so a
    # signature change still pairs as the same entity instead of removed+added.
    name = name.strip()
    if "(" in name and name.endswith(")") and not name.startswith("("):
        name = name[: name.index("(")]
    return name


def flatten(symbols: list[dict[str, Any]], path: str) -> list[Entity]:
    """Flatten DocumentSymbol[] (hierarchical) or SymbolInformation[] (flat)."""
    out: list[Entity] = []
    if not symbols:
        return out
    if "location" in symbols[0] and "selectionRange" not in symbols[0]:
        for s in symbols:
            loc = s["location"]
            r = loc["range"]
            p = uri_to_path(loc["uri"]) if loc.get("uri") else path
            out.append(
                Entity(
                    name=_clean_name(s["name"]),
                    kind=s.get("kind", 0),
                    path=p,
                    start_line=r["start"]["line"],
                    end_line=r["end"]["line"],
                    sel_line=r["start"]["line"],
                    sel_char=r["start"]["character"],
                    container=s.get("containerName") or "",
                )
            )
        _infer_nesting(out)
        return [e for e in out if e.kind not in NOISE]

    def walk(items: list[dict[str, Any]], container: str, depth: int, in_callable: bool, parent: Entity | None) -> None:
        for s in items:
            kind = s.get("kind", 0)
            r = s["range"]
            sel = s.get("selectionRange", r)
            name = _clean_name(s["name"])
            cont = container
            # gopls: "(*Server).Handle" -> container "Server", name "Handle"
            if name.startswith("(") and ")." in name:
                recv, _, meth = name.partition(").")
                cont = recv.lstrip("(*").strip()
                name = meth
            ent = Entity(
                name=name,
                kind=kind,
                path=path,
                start_line=r["start"]["line"],
                end_line=r["end"]["line"],
                sel_line=sel["start"]["line"],
                sel_char=sel["start"]["character"],
                container=cont,
                detail=s.get("detail") or "",
                depth=depth,
                in_callable=in_callable,
            )
            if kind not in NOISE:
                out.append(ent)
                if parent is not None:
                    parent.children.append(ent)
            child_container = ent.qualname if kind not in CONTAINERS or kind == NAMESPACE else container
            if kind in (FILE, MODULE, PACKAGE):
                child_container = container
            walk(
                s.get("children") or [],
                child_container,
                depth + 1,
                in_callable or kind in CALLABLE,
                ent if kind not in NOISE else parent,
            )

    walk(symbols, "", 0, False, None)
    return out


def _infer_nesting(ents: list[Entity]) -> None:
    ents.sort(key=lambda e: (e.start_line, -e.end_line))
    stack: list[Entity] = []
    for e in ents:
        while stack and not (stack[-1].start_line <= e.start_line and e.end_line <= stack[-1].end_line):
            stack.pop()
        e.depth = len(stack)
        e.in_callable = any(s.kind in CALLABLE for s in stack)
        if stack:
            stack[-1].children.append(e)
        stack.append(e)


def is_entity(e: Entity) -> bool:
    """Entities we report on: callables, types, and non-local data."""
    if e.kind in CONTAINERS and e.kind != NAMESPACE:
        return False
    if e.kind == NAMESPACE:
        return False
    if e.in_callable and e.kind not in CALLABLE:
        return False  # locals
    return e.kind in CALLABLE or e.kind in TYPES or e.kind in DATA


class SymbolIndex:
    """Per-file index answering "which entity encloses this line"."""

    def __init__(self, entities: list[Entity]):
        self.entities = [e for e in entities if is_entity(e)]
        self._sorted = sorted(self.entities, key=lambda e: e.start_line)
        self._starts = [e.start_line for e in self._sorted]

    def innermost(self, line: int, *, callable_only: bool = False) -> Entity | None:
        best: Entity | None = None
        idx = bisect.bisect_right(self._starts, line)
        for e in self._sorted[:idx]:
            if not e.contains_line(line):
                continue
            if callable_only and not e.is_callable:
                continue
            if best is None or e.span < best.span or (e.span == best.span and e.depth > best.depth):
                best = e
        return best

    def enclosing_for_impact(self, line: int) -> Entity | None:
        """Innermost callable; else innermost type/data entity."""
        return self.innermost(line, callable_only=True) or self.innermost(line)

    def overlapping(self, start: int, end: int) -> list[Entity]:
        return [e for e in self.entities if e.start_line <= end and start <= e.end_line]

    def by_qualname(self, qualname: str) -> list[Entity]:
        return [e for e in self.entities if e.qualname == qualname]
