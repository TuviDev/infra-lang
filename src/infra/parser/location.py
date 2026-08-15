"""Source location type shared across the compiler."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLocation:
    """A position range within a source file."""

    file: str
    line: int
    column: int
    end_line: int = 0
    end_column: int = 0

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"
