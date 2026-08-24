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
    "|pipeline|environment|cluster|secret_store|resource"
)

# v0.5.0: secret_store / resource use *quoted* names (`secret_store "v"`),
# so the name capture tolerates an optional pair of double quotes. Group 1 is
# the kind keyword, group 2 the (bare or quoted) name, exactly as before.
_BLOCK_RE = re.compile(
    r"\s*(" + BLOCK_KEYWORDS + r")\s+\"?([A-Za-z_][A-Za-z0-9_-]*)\"?"
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
    # Clamp the cursor to the line so an LSP position past the end of a short
    # line (common while editing) never raises IndexError.
    char = max(0, min(char, len(line)))
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


def symbol_at(source: str, line: int, char: int) -> Optional[str]:
    """Return the identifier under the cursor, if any."""
    lines = source.splitlines()
    if line < 0 or line >= len(lines):
        return None
    return _word_at(lines[line], char) or None


def symbol_range(source: str, line: int, char: int) -> Optional[Range]:
    """Return the ``Range`` of the identifier under the cursor, if any."""
    lines = source.splitlines()
    if line < 0 or line >= len(lines):
        return None
    text = lines[line]
    start, end = _word_span(text, char)
    if start == end:
        return None
    return Range(
        start=Position(line=line, character=start),
        end=Position(line=line, character=end),
    )


def _word_span(line: str, char: int) -> Tuple[int, int]:
    char = max(0, min(char, len(line)))
    start = char
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_-"):
        start -= 1
    end = char
    while end < len(line) and (line[end].isalnum() or line[end] in "_-"):
        end += 1
    return start, end


def highlight_ranges(
    source: str, line: int, char: int
) -> Tuple[Optional[str], List[Tuple[Range, str]]]:
    """Return ``(name, [(range, kind)])`` for occurrences of the symbol under
    the cursor in ``source`` (same file only).

    ``kind`` is ``"write"`` for a definition line, ``"read"`` otherwise.
    Word-boundary aware: renaming ``api`` never highlights ``api-2``/``my_api``.
    Returns ``(None, [])`` when the cursor is not on a symbol.
    """
    name = symbol_at(source, line, char)
    if not name:
        return None, []
    ranges: List[Tuple[Range, str]] = []
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])"
    )
    for i, ln in enumerate(source.splitlines()):
        stripped = ln.split("#", 1)[0]
        is_def = bool(_BLOCK_RE.match(stripped))
        for mm in pattern.finditer(stripped):
            kind = "write" if is_def else "read"
            ranges.append(
                (
                    Range(
                        start=Position(line=i, character=mm.start()),
                        end=Position(line=i, character=mm.end()),
                    ),
                    kind,
                )
            )
    return name, ranges


def rename_edits(
    source: str, target_name: str, new_name: str
) -> List[Tuple[Range, str]]:
    """Single-line edits to rename ``target_name`` (definition + references).

    Returns a list of ``(Range, replacement_text)`` pairs that, applied to the
    document, replace every occurrence of ``target_name`` with ``new_name``.
    Comments are ignored (a comment mentioning the name is left untouched).
    """
    edits: List[Tuple[Range, str]] = []
    for i, line in enumerate(source.splitlines()):
        stripped = line.split("#", 1)[0]
        m = _BLOCK_RE.match(stripped)
        if m and m.group(2) == target_name:
            start = stripped.find(target_name)
            edits.append(
                (
                    Range(
                        start=Position(line=i, character=start),
                        end=Position(line=i, character=start + len(target_name)),
                    ),
                    new_name,
                )
            )
            continue
        for mm in re.finditer(
            rf"(?<![A-Za-z0-9_-]){re.escape(target_name)}(?![A-Za-z0-9_-])",
            stripped,
        ):
            edits.append(
                (
                    Range(
                        start=Position(line=i, character=mm.start()),
                        end=Position(line=i, character=mm.end()),
                    ),
                    new_name,
                )
            )
    return edits


def rename_symbol(source: str, target_name: str, new_name: str) -> str:
    """Apply ``rename_edits`` and return the renamed document text."""
    edits = rename_edits(source, target_name, new_name)
    by_line: dict[int, List[Tuple[int, int, str]]] = {}
    for rng, text in edits:
        by_line.setdefault(rng.start.line, []).append(
            (rng.start.character, rng.end.character, text)
        )
    lines = source.splitlines()
    out: List[str] = []
    for i, line in enumerate(lines):
        pending = sorted(by_line.get(i, []), key=lambda e: e[0], reverse=True)
        new = line
        for start, end, text in pending:
            new = new[:start] + text + new[end:]
        out.append(new)
    return "\n".join(out)
