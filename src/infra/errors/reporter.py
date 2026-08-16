"""Pretty, Rust/Elm-style error reporting.

Uses ``rich`` when available and falls back to plain text.
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from infra.analyzer.validator import ValidationResult

from infra.errors.exceptions import (
    InfraCompileError,
    InfraError,
    InfraLexError,
    InfraParseError,
    ValidationError,
    ValidationWarning,
)

try:
    from rich.text import Text

    _RICH = True
except Exception:  # pragma: no cover
    _RICH = False


class ErrorReporter:
    """Renders compiler errors with source context and helpful hints."""

    def __init__(self, use_color: bool = True, max_errors: int = 10) -> None:
        self.use_color = use_color and _RICH
        self.max_errors = max_errors

    # ------------------------------------------------------------------ #
    def report_error(self, error: InfraError, source: str) -> str:
        if isinstance(error, InfraLexError):
            return self.report_lex_error(error, source)
        if isinstance(error, InfraParseError):
            return self.report_parse_error(error, source)
        if isinstance(error, InfraCompileError):
            return self.report_compile_error(error, source)
        return self._line(
            error.message, error.file, error.line, error.column, source, "E000"
        )

    def report_lex_error(self, error: InfraLexError, source: str) -> str:
        return self._line(
            f"Unexpected character {error.unexpected_char!r}",
            error.file,
            error.line,
            error.column,
            source,
            "E001",
            hint="This character cannot begin any token in Infra.",
        )

    def report_parse_error(self, error: InfraParseError, source: str) -> str:
        expected = ", ".join(f"'{e}'" for e in error.expected[:8]) or "end of input"
        return self._line(
            f"Unexpected {error.got!r}; expected one of: {expected}",
            error.file,
            error.line,
            error.column,
            source,
            "E002",
        )

    def report_semantic_errors(
        self,
        errors: List[ValidationError],
        warnings: List[ValidationWarning],
        source: str,
    ) -> str:
        lines = []
        for e in errors[: self.max_errors]:
            lines.append(self._validation(e, source, is_error=True))
        if len(errors) > self.max_errors:
            lines.append(f"... and {len(errors) - self.max_errors} more error(s)")
        for w in warnings[: self.max_errors]:
            lines.append(self._validation(w, source, is_error=False))
        return "\n\n".join(lines)

    def report_compile_error(self, error: InfraCompileError, source: str) -> str:
        return self._line(
            f"Compile error ({error.backend}): {error.message}",
            error.file,
            error.line,
            error.column,
            source,
            "E100",
        )

    # ------------------------------------------------------------------ #
    def _validation(
        self,
        e: ValidationError | ValidationWarning,
        source: str,
        is_error: bool,
    ) -> str:
        kind = "error" if is_error else "warning"
        code = e.code
        line = e.location.line if e.location else None
        col = e.location.column if e.location else None
        file = e.location.file if e.location else None
        text = self._line(
            e.message, file, line, col, source, code, hint=e.hint, kind=kind
        )
        return text

    def _line(
        self,
        message: str,
        file: Optional[str],
        line: Optional[int],
        column: Optional[int],
        source: str,
        code: str,
        hint: Optional[str] = None,
        kind: str = "error",
    ) -> str:
        out = []
        label = f"{kind}[{code}]"
        if self.use_color:
            out.append(f"[bold red]{label}[/bold red] {message}")
        else:
            out.append(f"{label}: {message}")
        if file and line:
            out.append(f"  --> {file}:{line}:{column or 1}")
            src_line = get_source_line(source, line)
            if src_line is not None:
                out.append("   |")
                out.append(f" {line:>3} | {src_line.rstrip()}")
                if column:
                    pad = max(column - 1, 0)
                    caret = "^" if self.use_color else "^"
                    out.append(f"     | {' ' * pad}{caret}")
        if hint:
            out.append(f"   = hint: {hint}")
        return "\n".join(out)

    def format_multiple_errors(
        self, errors: List[InfraError], source: str, max: int = 10
    ) -> str:
        rendered = [self.report_error(e, source) for e in errors[:max]]
        if len(errors) > max:
            rendered.append(f"... and {len(errors) - max} more error(s)")
        return "\n\n".join(rendered)

    def format_as_json(self, result: "ValidationResult") -> str:
        """Serialize a ValidationResult to a JSON string."""
        import json

        return json.dumps(
            {
                "valid": result.is_valid,
                "errors": [e.to_dict() for e in result.errors],
                "warnings": [w.to_dict() for w in result.warnings],
            },
            indent=2,
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def get_source_line(source: str, line: int) -> Optional[str]:
    """Return the text of a given 1-based line, or None if out of range."""
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1]
    return None


def highlight_range(line: str, col_start: int, col_end: int, color: str = "red") -> str:
    """Return a line with the given (1-based) column range highlighted."""
    if _RICH:
        t = Text(line)
        t.stylize(f"bold {color}", col_start - 1, col_end)
        return str(t)
    return line


def suggest_similar(name: str, candidates: List[str]) -> Optional[str]:
    """Return the closest match to *name* among candidates, if close enough."""
    if not candidates:
        return None
    matches = get_close_matches(name, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None
