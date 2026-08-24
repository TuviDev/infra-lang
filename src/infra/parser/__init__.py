"""Parser entry point: turns Infra source text into an AST Program."""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any, Optional, cast

from lark import Lark
from lark.exceptions import UnexpectedCharacters, UnexpectedToken

from infra.errors.exceptions import InfraLexError, InfraParseError
from infra.parser import ast_nodes as n
from infra.parser.transformer import InfraTransformer, _set_file

#: Top-level block keywords used to detect / suggest on unknown keywords.
_TOP_LEVEL_KEYWORDS = (
    "service",
    "database",
    "cache",
    "queue",
    "storage",
    "network",
    "secret",
    "config",
    "pipeline",
    "environment",
    "cluster",
    "import",
    "from",
    "const",
)
_TOP_LEVEL_KEYWORD_TOKENS = {
    "SERVICE",
    "DATABASE",
    "CACHE",
    "QUEUE",
    "STORAGE",
    "NETWORK",
    "SECRET",
    "CONFIG",
    "PIPELINE",
    "ENVIRONMENT",
    "CLUSTER",
    "IMPORT",
    "FROM",
    "CONST",
}
_IDENT_RE_STRICT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


def _source_prefix(source: str, line: Optional[int], column: Optional[int]) -> str:
    """Return the source text strictly before the given 1-based position."""
    if not line or not column:
        return source
    lines = source.split("\n")
    head = lines[: max(0, line - 1)]
    cur = lines[line - 1] if line - 1 < len(lines) else ""
    return "\n".join(head + [cur[: max(0, column - 1)]])


def _find_open_block_line(prefix: str) -> Optional[int]:
    """Find the line of the innermost unclosed ``{`` in *prefix*."""
    depth = 0
    last_open = None
    for i, ch in enumerate(prefix):
        if ch == "{":
            depth += 1
            last_open = i
        elif ch == "}":
            depth = max(0, depth - 1)
            if depth == 0:
                last_open = None
    if last_open is None:
        return None
    return prefix.count("\n", 0, last_open) + 1


def _field_awaiting_value(prefix: str) -> Optional[str]:
    """If *prefix* ends with ``field:`` return the field name, else None."""
    stripped = prefix.rstrip()
    if not stripped.endswith(":"):
        return None
    m = re.search(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:?$", stripped[:-1].rstrip())
    return m.group(1) if m else None


def _last_field_word(prefix: str) -> Optional[str]:
    """Return the last field-name word in *prefix* (for a missing-colon hint).

    Used when the parser expects a COLON right after a keyword/field name (e.g.
    ``image "x"`` instead of ``image: "x"``). Returns the trailing word if it
    looks like a field identifier.
    """
    stripped = prefix.rstrip()
    if not stripped:
        return None
    # strip trailing punctuation that isn't part of the field name
    m = re.search(r"([A-Za-z_][A-Za-z0-9_-]*)\s*$", stripped)
    return m.group(1) if m else None


def _friendly_parse_message(exc: Any, source: str) -> Optional[str]:
    """Build a friendlier message for common parse mistakes, or None."""
    if not isinstance(exc, (UnexpectedToken, UnexpectedCharacters)):
        return None

    if isinstance(exc, UnexpectedCharacters):
        # Unknown/illegal leading character; surface it directly.
        char = getattr(exc, "char", "") or ""
        if char == "\ufeff":
            return "Source begins with a UTF-8 BOM. Re-save the file without a BOM."
        if char == '"':
            # A quote at this position usually means an unterminated string:
            # the lexer hit a stray `"` that couldn't start a token.
            return (
                "Unterminated string literal. "
                'Did you forget to close the double quote (")?'
            )
        return None

    expected = sorted(exc.expected or [])
    got = str(exc.token.value) if exc.token is not None else ""
    prefix = _source_prefix(source, exc.line, exc.column)

    # 1) Missing closing brace (end-of-input with RBRACE still expected).
    if got == "" and "RBRACE" in expected:
        line = _find_open_block_line(prefix)
        if line is not None:
            return (
                f"Missing closing brace. Did you forget to close the block "
                f"started at line {line}?"
            )

    # 2) Missing value after a field (`image:` with nothing following).
    if got in ("}", "") and "IDENTIFIER" in expected:
        field = _field_awaiting_value(prefix)
        if field is not None:
            return f"Expected a value after '{field}:'. Example: {field}: \"...\""

    # 3) Unknown keyword at a statement position.
    if (
        _IDENT_RE_STRICT.match(got)
        and "IDENTIFIER" not in expected
        and _TOP_LEVEL_KEYWORD_TOKENS.intersection(expected)
    ):
        if got not in _TOP_LEVEL_KEYWORDS:
            from difflib import get_close_matches

            suggestion = get_close_matches(got, _TOP_LEVEL_KEYWORDS, n=1, cutoff=0.6)
            base = f"Unknown keyword '{got}'."
            if suggestion:
                base += f" Did you mean '{suggestion[0]}'?"
            else:
                base += " Did you mean 'service', 'database', 'secret', ...?"
            return base

    # 4) Missing colon after a field name (`image "x"` instead of `image: "x"`).
    if expected == ["COLON"]:
        field = _last_field_word(prefix)
        if field is not None:
            return (
                f"Expected ':' after field name '{field}'. "
                f"Did you forget the colon? Example: {field}: \"...\""
            )

    return None

#: Path to the bundled Lark grammar.
DEFAULT_GRAMMAR = Path(__file__).resolve().parent.parent / "lexer" / "grammar.lark"
PRELUDE_PATH = Path(__file__).resolve().parent.parent / "stdlib" / "prelude.infra"

#: Cache of the parsed prelude program (load once).
_PRELUDE: Optional["n.Program"] = None


@functools.lru_cache(maxsize=1)
def _raw_lark() -> Lark:
    """Return the shared ``Lark`` instance for the bundled grammar.

    Compiling ``grammar.lark`` into a LALR parser costs ~0.7 s, so the
    instance is built exactly once and reused by every ``Parser`` created
    with the default grammar and by the import resolver (which previously
    re-compiled the grammar for every imported file). ``Lark.parse()`` is
    reentrant across independent inputs, so sharing is safe.
    """
    return Lark(
        DEFAULT_GRAMMAR.read_text(encoding="utf-8"),
        parser="lalr",
        propagate_positions=True,
        start="start",
    )


def _load_prelude() -> "n.Program":
    global _PRELUDE
    if _PRELUDE is None:
        source = PRELUDE_PATH.read_text(encoding="utf-8")
        _PRELUDE = _parser().parse(source, filename="<prelude>")
    return _PRELUDE


class Parser:
    """Parses Infra source into an AST using the bundled Lark grammar."""

    def __init__(self, grammar_path: Optional[Path] = None) -> None:
        path = Path(grammar_path) if grammar_path else DEFAULT_GRAMMAR
        self._grammar_path = path
        if grammar_path is None:
            # Default grammar: reuse the process-wide shared instance so the
            # grammar is compiled once, not once per Parser construction.
            self._lark = _raw_lark()
        else:
            with open(path, encoding="utf-8") as f:
                grammar = f.read()
            self._lark = Lark(
                grammar,
                parser="lalr",
                propagate_positions=True,
                start="start",
            )
        self._transformer = InfraTransformer()

    def parse(self, source: str, filename: str = "<string>") -> n.Program:
        """Parse *source* and return a :class:`Program` AST."""
        # Strip UTF-8 BOM if present (Windows editors / PowerShell add it).
        if source.startswith("\ufeff"):
            source = source[1:]
        try:
            tree = self._lark.parse(source)
        except UnexpectedCharacters as exc:
            friendly = _friendly_parse_message(exc, source)
            message = friendly or f"Unexpected character {exc.char!r}"
            raise InfraLexError(
                message=message,
                source=source,
                line=exc.line,
                column=exc.column,
                file=filename,
                unexpected_char=str(exc.char),
            ) from exc
        except UnexpectedToken as exc:
            friendly = _friendly_parse_message(exc, source)
            message = friendly or self._token_message(exc)
            raise InfraParseError(
                message=message,
                source=source,
                line=exc.line,
                column=exc.column,
                file=filename,
                expected=sorted(exc.expected or []),
                got=str(exc.token.value) if exc.token is not None else None,
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise InfraParseError(
                message=str(exc),
                source=source,
                line=1,
                column=1,
                file=filename,
                expected=[],
                got=None,
            ) from exc
        _set_file(filename)
        program = self._transformer.transform(tree)
        if not isinstance(program, n.Program):
            program = n.Program(statements=(program,) if program is not None else ())
        if filename != "<prelude>":
            prelude = _load_prelude()
            program = n.Program(
                statements=prelude.statements + program.statements,
                imports=program.imports,
                environments=program.environments,
            )
            # `_load_prelude` re-parses with filename="<prelude>", which
            # overwrites the current-file tracking; restore it so backends that
            # read the source name (e.g. the AUTO-GENERATED header) report the
            # real file, not the prelude.
            _set_file(filename)
        return program

    def parse_file(self, path: Path) -> n.Program:
        """Read and parse a file, resolving its imports."""
        path = Path(path)
        source = path.read_text(encoding="utf-8")
        program = self.parse(source, filename=path.name)
        try:
            from infra.resolver.imports import ImportResolver

            resolver = ImportResolver(base_path=path.parent)
            program = resolver.resolve(program, path)
        except FileNotFoundError:
            # re-raise as parse error for consistency with non-import parses
            from infra.errors.exceptions import InfraParseError

            raise InfraParseError(
                f"Could not resolve imports in '{path.name}'",
                source=source,
                file=path.name,
            )
        return program

    def parse_expression(self, source: str, filename: str = "<string>") -> n.Expression:
        """Parse a standalone expression (used by the REPL)."""
        parser = Lark(
            self._grammar_path.read_text(encoding="utf-8"),
            parser="lalr",
            propagate_positions=True,
            start="expression",
        )
        tree = parser.parse(source)
        transformed = InfraTransformer().transform(tree)
        return cast(n.Expression, transformed)

    @staticmethod
    def _token_message(exc: UnexpectedToken) -> str:
        got = exc.token.value if exc.token is not None else "end of input"
        expected = sorted(exc.expected or [])
        if expected:
            return f"Unexpected {got!r}; expected one of: " + ", ".join(
                f"'{e}'" for e in expected[:8]
            )
        return f"Unexpected {got!r}"


# --------------------------------------------------------------------------- #
# Module-level convenience functions
# --------------------------------------------------------------------------- #

_parser_instance: Optional[Parser] = None


def _parser() -> Parser:
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = Parser()
    return _parser_instance


def parse(source: str, filename: str = "<string>") -> n.Program:
    """Parse Infra source into a Program AST (convenience wrapper)."""
    return _parser().parse(source, filename=filename)


def parse_file(path: Path) -> n.Program:
    """Parse an Infra source file (convenience wrapper)."""
    return _parser().parse_file(path)


def parse_expression(source: str, filename: str = "<string>") -> n.Expression:
    """Parse a standalone expression (convenience wrapper)."""
    return _parser().parse_expression(source, filename=filename)


__all__ = ["Parser", "parse", "parse_file", "parse_expression", "DEFAULT_GRAMMAR"]
