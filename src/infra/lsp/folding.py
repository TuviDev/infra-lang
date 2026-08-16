"""Folding-range generation for the Infra Lang LSP.

Computes foldable regions for an ``.infra`` document:

- every ``{}`` block (top-level and nested), collapsed to the closing ``}``
  line, and
- runs of consecutive comment lines (type ``Comment``).

Line-based and tolerant of malformed input (an unbalanced brace just yields no
range for that region — never a crash).
"""

from __future__ import annotations

from typing import List

from lsprotocol.types import FoldingRange, FoldingRangeKind

#: Minimum block span (in lines) to be worth folding.
_MIN_BLOCK_SPAN = 2


def folding_ranges(source: str) -> List[FoldingRange]:
    """Return folding ranges for blocks and comment runs in ``source``.

    Tolerant: malformed braces are skipped; never raises.
    """
    lines = source.splitlines()
    ranges: List[FoldingRange] = []

    # ---- braces / blocks ---- #
    stack: List[tuple[int, str | None]] = []  # (start_line, kind)
    for i, line in enumerate(lines):
        stripped = line.split("#", 1)[0]
        for ch in stripped:
            if ch == "{":
                # Detect a comment/region marker? Blocks only here.
                stack.append((i, None))
            elif ch == "}":
                if stack:
                    start_line, _ = stack.pop()
                    if i - start_line >= _MIN_BLOCK_SPAN:
                        ranges.append(
                            FoldingRange(
                                start_line=start_line,
                                end_line=i,
                                kind=FoldingRangeKind.Region,
                            )
                        )

    # ---- consecutive comment runs ---- #
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("#"):
            j = i
            while j + 1 < len(lines) and lines[j + 1].lstrip().startswith("#"):
                j += 1
            if j - i >= 2:  # at least 3 comment lines to fold
                ranges.append(
                    FoldingRange(
                        start_line=i,
                        end_line=j,
                        kind=FoldingRangeKind.Comment,
                    )
                )
            i = j + 1
        else:
            i += 1

    ranges.sort(key=lambda r: (r.start_line, r.end_line))
    return ranges
