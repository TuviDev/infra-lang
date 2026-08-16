"""Signature help for the Infra Lang LSP.

In Infra, "signature help" shows the fields available inside a block. When the
cursor is inside ``service foo { | }``, the editor shows the fields of a
``service`` block (image, port, replicas, ...) with their types and short docs.

The available fields are derived from the same source of truth as completion
(``BLOCK_FIELDS``), so the two never drift. Fields already present on the
current line are de-emphasized.
"""

from __future__ import annotations

import re
from typing import List, Optional

from lsprotocol.types import (
    ParameterInformation,
    SignatureHelp,
    SignatureInformation,
)

from infra.lsp.completion import BLOCK_FIELDS, BLOCK_SUBBLOCKS

_BLOCK_OPEN_RE = re.compile(
    r"^\s*(service|database|cache|queue|storage|network|secret|config"
    r"|pipeline|environment|cluster)\b"
)


def _field_doc(block: str, field: str) -> str:
    from infra.lsp.server import FIELD_DOCS

    return FIELD_DOCS.get(field, "")


def signature_help_at(source: str, line: int, char: int) -> Optional[SignatureHelp]:
    """Return field signature help when the cursor is inside a block.

    Returns None when the cursor is not inside a recognizable block (so the
    client shows nothing, without crashing).
    """
    lines = source.splitlines()
    block = _current_block(lines, line)
    if block is None:
        return None

    fields = BLOCK_FIELDS.get(block, [])
    used = _used_fields_on_lines(lines, line, block)
    signatures: List[SignatureInformation] = []
    for field in fields:
        doc = _field_doc(block, field)
        label = field
        if field in BLOCK_SUBBLOCKS.get(block, []):
            label += " { ... }"
        if field in used:
            label += "  (set)"
        sig = SignatureInformation(
            label=label,
            documentation=doc or None,
            parameters=[ParameterInformation(label=field, documentation=doc or None)],
        )
        signatures.append(sig)

    if not signatures:
        return None
    return SignatureHelp(signatures=signatures, active_signature=0, active_parameter=0)


def _current_block(lines: List[str], line: int) -> Optional[str]:
    """Find the innermost block the cursor is inside, by brace balance.

    Walks the lines up to the cursor and returns the most recent block whose
    opening ``{`` has not been closed yet.
    """
    depth = 0
    current: Optional[str] = None
    for i in range(line + 1):
        text = lines[i] if i < len(lines) else ""
        stripped = text.split("#", 1)[0]
        m = _BLOCK_OPEN_RE.match(stripped)
        if m and depth == 0:
            current = m.group(1)
        for ch in stripped:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    depth = 0
    if depth >= 1 and current is not None:
        return current
    return None


def _used_fields_on_lines(
    lines: List[str], current_line: int, block: str
) -> set[str]:
    """Collect field names already set in the current block (before cursor)."""
    fields = set()
    field_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")
    # Walk backward from cursor to the block's opening line.
    depth = 0
    for i in range(current_line, -1, -1):
        stripped = lines[i].split("#", 1)[0]
        for ch in stripped:
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
        if depth < 0:
            # found the opening line of the block
            break
        m = field_re.match(stripped)
        if m:
            fields.add(m.group(1))
    return fields
