"""AST ↔ source round-trip utilities for ``infra doctor --fix`` (v0.9.0).

Editing strategy: parse → :func:`infra.analyzer.autofix.apply_fixes`
(pure AST transforms) → re-print with the canonical
:class:`infra.cli.printer.InfraPrinter`. Advisory comments (SEC003) are
attached to the printed image line *after* rendering, so the AST itself
stays comment-free.

Round-trip guarantee: ``print(parse(source))`` is idempotent (verified by
tests over 100+ realistic files), so files without any fixable finding
come through bit-identical; files with findings change only by the fixes.
"""

from __future__ import annotations

import difflib
import re
from typing import List, Optional, Sequence, Tuple

from infra.analyzer.autofix import FixResult, apply_fixes
from infra.cli.printer import InfraPrinter
from infra.parser import ast_nodes as n

_IMAGE_LINE_RE = re.compile(r'^(\s*image:\s*)(".+?")(\s*)$')


def print_source(program: n.Program) -> str:
    """:class:`InfraPrinter` rendering (canonical, idempotent)."""
    return InfraPrinter().print(program)


def attach_fix_comments(source: str, comments: List[Tuple[str, str]]) -> str:
    """Append advisory comments to matching ``image: "…"`` lines.

    ``comments`` carry ``(image, comment)`` pairs in AST order; each pair is
    attached to the next not-yet-annotated line whose image literal equals
    the recorded one, so duplicate images across services are still handled
    deterministically.
    """
    if not comments:
        return source
    pending = list(comments)
    out: List[str] = []
    for line in source.splitlines():
        match = _IMAGE_LINE_RE.match(line)
        if match is not None and pending:
            image = pending[0][0]
            if match.group(2) == f'"{image}"':
                _image, comment = pending.pop(0)
                line = f"{match.group(1)}{match.group(2)}  {comment}"
        out.append(line)
    # Unmatched comments (defensive; should never happen) go to the end.
    for _image, comment in pending:
        out.append(comment)
    return "\n".join(out) + ("\n" if source.endswith("\n") else "")


def compute_fixes(
    program: n.Program,
    *,
    only: Optional[Sequence[str]] = None,
    default_memory: Optional[str] = None,
) -> Tuple[FixResult, str, str]:
    """Compute fixes for *program* and return ``(result, old, new)`` sources.

    ``old`` is the canonical print of the input program; ``new`` is the
    print after fixes plus any advisory comments.
    """
    from infra.analyzer.autofix import DEFAULT_MEMORY

    old_source = print_source(program)
    result = apply_fixes(
        program,
        only=only,
        default_memory=default_memory or DEFAULT_MEMORY,
    )
    new_source = print_source(result.program)
    new_source = attach_fix_comments(new_source, result.comments)
    return result, old_source, new_source


def render_diff(
    old_source: str,
    new_source: str,
    *,
    from_name: str = "current",
    to_name: str = "fixed",
) -> str:
    """Plain unified diff between two canonical sources."""
    diff = difflib.unified_diff(
        old_source.splitlines(),
        new_source.splitlines(),
        fromfile=from_name,
        tofile=to_name,
        lineterm="",
    )
    return "\n".join(diff) + "\n"


def is_round_trip_stable(source: str) -> bool:
    """True when ``print(parse(source))`` is a fixed point (idempotent)."""
    from infra.parser import parse

    once = print_source(parse(source))
    twice = print_source(parse(once))
    return once == twice


__all__ = [
    "attach_fix_comments",
    "compute_fixes",
    "is_round_trip_stable",
    "print_source",
    "render_diff",
]
