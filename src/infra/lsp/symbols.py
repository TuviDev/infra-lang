"""Lightweight document symbol extraction for the Infra Lang LSP.

Provides:
- documentSymbol: an outline of top-level blocks (and their names).
- definition lookup: given a position, find the name of the block under the
  cursor and the range where that block's definition lives.

The extraction is heuristic (regex over ``<keyword> <name> {``) so it works on
incomplete input. Symbol positions are 0-based LSP line/character.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from lsprotocol.types import (
    DocumentSymbol,
    Position,
    Range,
    SymbolKind,
)

BLOCK_KEYWORDS = (
    "service|database|cache|queue|storage|network|secret|config"
    "|pipeline|environment|cluster"
)

_BLOCK_RE = re.compile(
    r"\s*(" + BLOCK_KEYWORDS + r")\s+([A-Za-z_][A-Za-z0-9_-]*)"
)


def _line_range(line: int, end_char: int = 40) -> Range:
    return Range(
        start=Position(line=line, character=0),
        end=Position(line=line, character=end_char),
    )


def document_symbols(source: str) -> List[DocumentSymbol]:
    """Return an outline of top-level blocks in the document."""
    symbols: List[DocumentSymbol] = []
    for i, line in enumerate(source.splitlines()):
        stripped = line.split("#", 1)[0]
        m = _BLOCK_RE.match(stripped)
        if m:
            kind_word, name = m.group(1), m.group(2)
            symbols.append(
                DocumentSymbol(
                    name=f"{kind_word} {name}",
                    kind=SymbolKind.Struct,
                    range=_line_range(i),
                    selection_range=_line_range(i, len(line.strip())),
                    detail=kind_word,
                )
            )
    return symbols


def _block_at_position(source: str, line: int, char: int) -> Optional[str]:
    """Return the name of the block whose definition line is under the cursor."""
    lines = source.splitlines()
    if line < 0 or line >= len(lines):
        return None
    stripped = lines[line].split("#", 1)[0]
    m = _BLOCK_RE.match(stripped)
    if not m:
        return None
    # accept the cursor anywhere on the definition line (keyword or name)
    return m.group(2)


def find_definition(
    source: str, line: int, char: int
) -> Optional[Tuple[str, int]]:
    """Return (block_name, definition_line) for the block under the cursor.

    If the cursor is on a reference inside a field (e.g. ``depends: [db]``),
    resolve to the definition line of ``db``. Returns None if unresolved.
    """
    lines = source.splitlines()
    name = _block_at_position(source, line, char)
    if name is not None:
        return (name, line)

    # try reference resolution: find the word under the cursor anywhere on the
    # line, then look for a matching block definition elsewhere.
    if line < 0 or line >= len(lines):
        return None
    line_text = lines[line]
    word = _word_at(line_text, char)
    if word:
        for i, ln in enumerate(source.splitlines()):
            m = _BLOCK_RE.match(ln.split("#", 1)[0])
            if m and m.group(2) == word:
                return (word, i)
    return None


def _word_at(line: str, char: int) -> str:
    start = char
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_-"):
        start -= 1
    end = char
    while end < len(line) and (line[end].isalnum() or line[end] in "_-"):
        end += 1
    return line[start:end]


def reference_ranges(source: str, target_name: str) -> List[Range]:
    """Find ranges where ``target_name`` is referenced in the document."""
    ranges: List[Range] = []
    for i, line in enumerate(source.splitlines()):
        stripped = line.split("#", 1)[0]
        # skip the definition line itself
        if _BLOCK_RE.match(stripped) and target_name in stripped:
            continue
        for m in re.finditer(rf"\b{re.escape(target_name)}\b", stripped):
            ranges.append(
                Range(
                    start=Position(line=i, character=m.start()),
                    end=Position(line=i, character=m.end()),
                )
            )
    return ranges
