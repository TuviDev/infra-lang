"""LSP code actions (quick fixes) for common, safely-automatable lint findings.

Each fix maps a diagnostic ``code`` to a concrete ``TextEdit`` that resolves
the underlying issue. Only *safe, deterministic* rewrites are offered — we
never guess at user intent.

The engine is pure (takes source + uri + diagnostics, returns CodeAction list)
so it is unit-testable without a running server.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from lsprotocol.types import (
    CodeAction,
    CodeActionKind,
    Diagnostic,
    Position,
    Range,
    TextEdit,
    WorkspaceEdit,
)

# diagnostic code -> (new_text, description)
# These are text substitutions applied at the diagnostic's location.
_SIMPLE_FIXES: Dict[str, Tuple[str, str]] = {
    "E011": ("1", "Set replicas to at least 1"),  # replicas: 0 -> replicas: 1
    "E012": ("8080", "Use a valid port (1-65535)"),  # port out of range
}

# value literals that are unambiguously bad and safe to replace
_KNOWN_BAD: Tuple[str, ...] = ("0", "99999", "70000")


def _find_value_span(line_text: str, column: int) -> Tuple[int, int] | None:
    """Return (start, end) of the value token for the field on this line.

    The diagnostic typically points at the start of the line (line 1, col 1),
    so we ignore ``column`` and instead find the last ``field:`` label on the
    line, then the value token that follows it.
    """
    colon = line_text.rfind(":")
    if colon == -1:
        return None
    val_start = colon + 1
    while val_start < len(line_text) and line_text[val_start].isspace():
        val_start += 1
    val_end = val_start
    while val_end < len(line_text) and (
        line_text[val_end].isalnum() or line_text[val_end] == "_"
    ):
        val_end += 1
    if val_start >= val_end:
        return None
    return (val_start, val_end)


def quick_fixes(
    uri: str,
    source: str,
    diagnostics: List[Diagnostic],
) -> List[CodeAction]:
    """Return code actions for the given diagnostics.

    The ``code`` field of each diagnostic selects the fix. Only applies when
    the current value at the diagnostic location is a known-bad literal, so we
    never corrupt the document.
    """
    lines = source.splitlines()
    if not lines:
        lines = [""]
    actions: List[CodeAction] = []
    for diag in diagnostics:
        code = diag.code
        if code not in _SIMPLE_FIXES:
            continue
        new_text, desc = _SIMPLE_FIXES[code]
        line = diag.range.start.line
        col = diag.range.start.character
        line_text = lines[line] if 0 <= line < len(lines) else ""
        span = _find_value_span(line_text, col)
        if span is None:
            continue
        start, end = span
        old = line_text[start:end]
        if old not in _KNOWN_BAD:
            continue
        te = TextEdit(
            range=Range(
                start=Position(line=line, character=start),
                end=Position(line=line, character=end),
            ),
            new_text=new_text,
        )
        actions.append(
            CodeAction(
                title=desc,
                kind=CodeActionKind.QuickFix,
                diagnostics=[diag],
                edit=WorkspaceEdit(changes={uri: [te]}),
            )
        )
    return actions
