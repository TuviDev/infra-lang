"""Exception hierarchy for the Infra compiler.

All compiler errors derive from :class:`InfraError` and expose a location plus
machine-readable conversion helpers (:meth:`to_dict`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from infra.parser.location import SourceLocation


@dataclass
class InfraError(Exception):
    """Base class for every compiler error."""

    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None

    @property
    def location(self) -> "Optional[SourceLocation]":
        if self.line is not None:
            from infra.parser.location import SourceLocation

            return SourceLocation(
                file=self.file or "<string>",
                line=self.line or 1,
                column=self.column or 1,
            )
        return None

    def __str__(self) -> str:
        where = ""
        if self.file and self.line:
            where = f" at {self.file}:{self.line}:{self.column or 1}"
        return f"{self.__class__.__name__}: {self.message}{where}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.__class__.__name__,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
        }


class InfraLexError(InfraError):
    """A tokenization error."""

    unexpected_char: str = ""

    def __init__(
        self,
        message: str,
        source: str = "",
        line: Optional[int] = None,
        column: Optional[int] = None,
        file: Optional[str] = None,
        unexpected_char: str = "",
    ) -> None:
        super().__init__(message, file=file, line=line, column=column)
        self.source = source
        self.unexpected_char = unexpected_char


class InfraParseError(InfraError):
    """A syntax / parsing error."""

    expected: List[str]
    got: Optional[str]
    source: str

    def __init__(
        self,
        message: str,
        source: str = "",
        line: Optional[int] = None,
        column: Optional[int] = None,
        file: Optional[str] = None,
        expected: Optional[List[str]] = None,
        got: Optional[str] = None,
    ) -> None:
        super().__init__(message, file=file, line=line, column=column)
        self.source = source
        self.expected = expected or []
        self.got = got

    def _render_context(self, context_lines: int = 2) -> str:
        """Render a few lines of the source around the error with a caret."""
        if not self.source or not self.line:
            return ""
        lines = self.source.splitlines()
        start = max(0, self.line - 1 - context_lines)
        end = min(len(lines), self.line + context_lines)
        gutter_w = len(str(end))
        out: List[str] = []
        for i in range(start, end):
            n = i + 1
            marker = "  "
            if n == self.line:
                marker = "> "
            text = lines[i] if i < len(lines) else ""
            out.append(f"{marker}{n:>{gutter_w}} | {text}")
            if n == self.line and self.column:
                pad = self.column - 1
                caret = " " * max(0, pad) + "^"
                # extend caret to cover the offending token if we know its length
                if self.got:
                    caret = " " * max(0, pad) + "^" * max(1, len(self.got))
                out.append(f"{'':>{gutter_w + 1}} | {caret}")
        return "\n".join(out)

    def __str__(self) -> str:
        where = ""
        if self.file and self.line:
            where = f" at {self.file}:{self.line}:{self.column or 1}"
        head = f"error[PARSE]: {self.message}{where}"
        ctx = self._render_context()
        details: List[str] = [head]
        if ctx:
            details.append(ctx)
        if self.expected:
            details.append(f"  = Expected: {', '.join(self.expected)}")
        if self.got is not None:
            details.append(f"  = Got: {self.got!r}")
        return "\n".join(details)


@dataclass
class ValidationError:
    """A semantic validation error."""

    message: str
    location: Optional[SourceLocation] = None
    code: str = "E000"
    hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "file": self.location.file if self.location else None,
            "line": self.location.line if self.location else None,
            "column": self.location.column if self.location else None,
        }


@dataclass
class ValidationWarning:
    """A semantic validation warning."""

    message: str
    location: Optional[SourceLocation] = None
    code: str = "W000"
    hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "file": self.location.file if self.location else None,
            "line": self.location.line if self.location else None,
            "column": self.location.column if self.location else None,
        }


class InfraSemanticError(InfraError):
    """Carries a set of validation errors/warnings."""

    errors: List[ValidationError]
    warnings: List[ValidationWarning]

    def __init__(
        self,
        errors: List[ValidationError],
        warnings: Optional[List[ValidationWarning]] = None,
    ) -> None:
        self.errors = errors
        self.warnings = warnings or []
        msg = f"{len(errors)} semantic error(s)"
        super().__init__(msg)


class InfraCompileError(InfraError):
    """A backend compilation error."""

    backend: str
    node: Optional[Any] = None
    reason: str = ""

    def __init__(
        self,
        message: str,
        backend: str = "unknown",
        node: Optional[Any] = None,
        reason: str = "",
        file: Optional[str] = None,
        line: Optional[int] = None,
        column: Optional[int] = None,
    ) -> None:
        super().__init__(message, file=file, line=line, column=column)
        self.backend = backend
        self.node = node
        self.reason = reason


class InfraRuntimeError(InfraError):
    """A runtime/evaluation error (REPL, expression evaluation)."""

    expression: str
    reason: str = ""

    def __init__(
        self,
        message: str,
        expression: str = "",
        reason: str = "",
        file: Optional[str] = None,
        line: Optional[int] = None,
        column: Optional[int] = None,
    ) -> None:
        super().__init__(message, file=file, line=line, column=column)
        self.expression = expression
        self.reason = reason
