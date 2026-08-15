"""Parser entry point: turns Infra source text into an AST Program."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lark import Lark
from lark.exceptions import UnexpectedCharacters, UnexpectedToken

from infra.errors.exceptions import InfraLexError, InfraParseError
from infra.parser import ast_nodes as n
from infra.parser.transformer import InfraTransformer, _set_file

#: Path to the bundled Lark grammar.
DEFAULT_GRAMMAR = Path(__file__).resolve().parent.parent / "lexer" / "grammar.lark"
PRELUDE_PATH = Path(__file__).resolve().parent.parent / "stdlib" / "prelude.infra"

#: Cache of the parsed prelude program (load once).
_PRELUDE: Optional["n.Program"] = None


def _load_prelude() -> "n.Program":
    global _PRELUDE
    if _PRELUDE is None:
        source = PRELUDE_PATH.read_text()
        _PRELUDE = _parser().parse(source, filename="<prelude>")
    return _PRELUDE


class Parser:
    """Parses Infra source into an AST using the bundled Lark grammar."""

    def __init__(self, grammar_path: Optional[Path] = None) -> None:
        path = Path(grammar_path) if grammar_path else DEFAULT_GRAMMAR
        self._grammar_path = path
        with open(path) as f:
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
        try:
            tree = self._lark.parse(source)
        except UnexpectedCharacters as exc:
            raise InfraLexError(
                message=f"Unexpected character {exc.char!r}",
                source=source,
                line=exc.line,
                column=exc.column,
                file=filename,
                unexpected_char=str(exc.char),
            ) from exc
        except UnexpectedToken as exc:
            raise InfraParseError(
                message=self._token_message(exc),
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
            )
        return program

    def parse_file(self, path: Path) -> n.Program:
        """Read and parse a file, resolving its imports."""
        path = Path(path)
        source = path.read_text()
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
            self._grammar_path.read_text(),
            parser="lalr",
            propagate_positions=True,
            start="expression",
        )
        tree = parser.parse(source)
        transformed = InfraTransformer().transform(tree)
        return transformed

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
