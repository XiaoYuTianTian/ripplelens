"""Minimal SCIP index reader (protobuf wire format, no generated code or protoc needed).

Only the fields the judge needs (scip.proto):
  Index:      documents = 2
  Document:   relative_path = 1, occurrences = 2, language = 4
  Occurrence: range = 1 (packed int32) or a structured range message = 8, symbol = 2, symbol_roles = 3
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFINITION = 0x1
IMPORT = 0x2
GENERATED = 0x10
FORWARD_DEFINITION = 0x40


@dataclass(slots=True)
class Occurrence:
    line: int  # 0-based
    char: int
    symbol: str
    roles: int


@dataclass
class Document:
    path: str  # relative, forward slashes
    language: str
    occurrences: list[Occurrence]


def _varint(buf: memoryview, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: memoryview):
    """Yield (field_number, wire_type, value) where value is int or memoryview."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
            yield field, wt, v
        elif wt == 2:
            ln, i = _varint(buf, i)
            yield field, wt, buf[i : i + ln]
            i += ln
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")


def _occurrence(buf: memoryview) -> Occurrence | None:
    rng: list[int] = []
    symbol = ""
    roles = 0
    for f, wt, v in _fields(buf):
        if f == 1:
            if wt == 2:
                j = 0
                while j < len(v):
                    x, j = _varint(v, j)
                    rng.append(x)
            else:
                rng.append(v)
        elif f == 2:
            symbol = bytes(v).decode("utf-8", "replace")
        elif f == 3:
            roles = v
        elif f == 8 and wt == 2 and not rng:
            # newer indexers: structured range message (1 = start line, 2 = start character; zeros omitted)
            start = {1: 0, 2: 0}
            for f2, wt2, v2 in _fields(v):
                if f2 in start and wt2 == 0:
                    start[f2] = v2
            rng = [start[1], start[2], 0]
    if len(rng) < 3 or not symbol or symbol.startswith("local "):
        return None
    return Occurrence(rng[0], rng[1], symbol, roles)


def read_index(path: str | Path) -> list[Document]:
    data = memoryview(Path(path).read_bytes())
    docs = []
    for f, wt, v in _fields(data):
        if f != 2 or wt != 2:
            continue
        rel, lang, occs = "", "", []
        for f2, wt2, v2 in _fields(v):
            if f2 == 1:
                rel = bytes(v2).decode("utf-8", "replace")
            elif f2 == 4:
                lang = bytes(v2).decode("utf-8", "replace")
            elif f2 == 2 and wt2 == 2:
                o = _occurrence(v2)
                if o is not None:
                    occs.append(o)
        docs.append(Document(rel.replace("\\", "/"), lang, occs))
    return docs
