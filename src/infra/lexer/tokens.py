"""Token definitions for the Infra Language.

The actual lexer is powered by Lark (see ``grammar.lark``), but this module
provides the canonical, editor- and tooling-friendly view of the language's
token types, keywords, operators and units, plus a small ``Token`` record used
throughout the compiler (REPL, formatter, errors).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TokenType(str, Enum):
    """All token categories understood by the Infra language."""

    # Literals
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    STRING = "STRING"
    TEMPLATE_STRING = "TEMPLATE_STRING"
    BOOLEAN = "BOOLEAN"
    NULL = "NULL"
    DURATION = "DURATION"
    RESOURCE_VALUE = "RESOURCE_VALUE"
    PERCENTAGE = "PERCENTAGE"

    # Names & structure
    IDENTIFIER = "IDENTIFIER"
    KEYWORD = "KEYWORD"
    DECORATOR = "DECORATOR"
    COMMENT = "COMMENT"

    # Operators
    PLUS = "PLUS"  # +
    MINUS = "MINUS"  # -
    STAR = "STAR"  # *
    SLASH = "SLASH"  # /
    PERCENT = "PERCENT"  # %
    POW = "POW"  # **
    EQ = "EQ"  # ==
    NEQ = "NEQ"  # !=
    LT = "LT"  # <
    LE = "LE"  # <=
    GT = "GT"  # >
    GE = "GE"  # >=
    AND = "AND"  # &&
    OR = "OR"  # ||
    NOT = "NOT"  # !
    ASSIGN = "ASSIGN"  # =
    ARROW = "ARROW"  # ->

    # Punctuation
    LPAREN = "LPAREN"  # (
    RPAREN = "RPAREN"  # )
    LBRACE = "LBRACE"  # {
    RBRACE = "RBRACE"  # }
    LBRACKET = "LBRACKET"  # [
    RBRACKET = "RBRACKET"  # ]
    COMMA = "COMMA"  # ,
    COLON = "COLON"  # :
    DOT = "DOT"  # .
    AT = "AT"  # @
    UNDERSCORE = "UNDERSCORE"  # _

    # Misc
    EOF = "EOF"
    NEWLINE = "NEWLINE"
    UNKNOWN = "UNKNOWN"


#: Strings that introduce reserved language keywords.
KEYWORDS: dict[str, TokenType] = {
    "service": TokenType.KEYWORD,
    "database": TokenType.KEYWORD,
    "cache": TokenType.KEYWORD,
    "queue": TokenType.KEYWORD,
    "storage": TokenType.KEYWORD,
    "network": TokenType.KEYWORD,
    "secret": TokenType.KEYWORD,
    "config": TokenType.KEYWORD,
    "pipeline": TokenType.KEYWORD,
    "environment": TokenType.KEYWORD,
    "cluster": TokenType.KEYWORD,
    "let": TokenType.KEYWORD,
    "const": TokenType.KEYWORD,
    "import": TokenType.KEYWORD,
    "from": TokenType.KEYWORD,
    "as": TokenType.KEYWORD,
    "match": TokenType.KEYWORD,
    "if": TokenType.KEYWORD,
    "then": TokenType.KEYWORD,
    "else": TokenType.KEYWORD,
    "true": TokenType.BOOLEAN,
    "false": TokenType.BOOLEAN,
    "null": TokenType.NULL,
    "in": TokenType.KEYWORD,
}

#: Operator strings to their token type.
OPERATORS: dict[str, TokenType] = {
    "+": TokenType.PLUS,
    "-": TokenType.MINUS,
    "*": TokenType.STAR,
    "/": TokenType.SLASH,
    "%": TokenType.PERCENT,
    "**": TokenType.POW,
    "==": TokenType.EQ,
    "!=": TokenType.NEQ,
    "<": TokenType.LT,
    "<=": TokenType.LE,
    ">": TokenType.GT,
    ">=": TokenType.GE,
    "&&": TokenType.AND,
    "||": TokenType.OR,
    "!": TokenType.NOT,
    "=": TokenType.ASSIGN,
    "->": TokenType.ARROW,
}

#: Time units and their factor in seconds.
UNITS_TIME: dict[str, float] = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}

#: Resource units (Kubernetes-style) to a canonical form.
UNITS_RESOURCE: dict[str, str] = {
    "Ki": "Ki",
    "Mi": "Mi",
    "Gi": "Gi",
    "Ti": "Ti",
    "KiB": "Ki",
    "MiB": "Mi",
    "GiB": "Gi",
    "m": "m",
    "n": "n",
}


@dataclass(frozen=True)
class Token:
    """A single lexical token with source location information."""

    type: TokenType
    value: str
    line: int = 1
    column: int = 1
    file: Optional[str] = None
    # Lark Token metadata, if this token was produced by the lexer.
    meta: dict = field(default_factory=dict, compare=False)  # type: ignore[type-arg]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.type.value}, {self.value!r}, {self.line}:{self.column})"


def is_keyword(string: str) -> bool:
    """Return True if *string* is a reserved keyword of the language."""
    return string in KEYWORDS


def get_token_description(type_: TokenType) -> str:
    """Return a human-readable description of a token type (for error messages)."""
    descriptions: dict[TokenType, str] = {
        TokenType.INTEGER: "an integer number",
        TokenType.FLOAT: "a floating-point number",
        TokenType.STRING: "a string literal",
        TokenType.TEMPLATE_STRING: "a template string",
        TokenType.BOOLEAN: "a boolean literal (true/false)",
        TokenType.NULL: "the null literal",
        TokenType.DURATION: "a time duration (e.g. 30s, 5m)",
        TokenType.RESOURCE_VALUE: "a resource value (e.g. 128Mi, 500m)",
        TokenType.PERCENTAGE: "a percentage (e.g. 50%)",
        TokenType.IDENTIFIER: "an identifier",
        TokenType.KEYWORD: "a keyword",
        TokenType.DECORATOR: "a decorator (@name)",
        TokenType.PLUS: "operator '+'",
        TokenType.MINUS: "operator '-'",
        TokenType.STAR: "operator '*'",
        TokenType.SLASH: "operator '/'",
        TokenType.EQ: "operator '=='",
        TokenType.NEQ: "operator '!='",
        TokenType.ASSIGN: "operator '='",
        TokenType.ARROW: "operator '->'",
        TokenType.LPAREN: "'('",
        TokenType.RPAREN: "')'",
        TokenType.LBRACE: "'{'",
        TokenType.RBRACE: "'}'",
        TokenType.LBRACKET: "'['",
        TokenType.RBRACKET: "']'",
        TokenType.COMMA: "','",
        TokenType.COLON: "':'",
        TokenType.DOT: "'.'",
        TokenType.EOF: "end of input",
    }
    return descriptions.get(type_, f"token of type {type_.value}")
