"""Semantic-token generation for the Infra Lang LSP.

Produces LSP semantic tokens (line/start/length/type) for an ``.infra``
document. Semantic tokens give editors syntax highlighting that is more precise
than a TextMate grammar — it distinguishes block keywords, resource names,
field names, type values, strings and numbers.

The tokenizer is **line-based and deterministic**, not AST-based, for two
reasons:

- It must tolerate malformed / incomplete documents (an AST parse would raise
  on half-typed input; semantic tokens should never crash the editor).
- It needs *every* token (including string values and numbers inside
  expressions), which the AST does not retain positionally.

Token types are mapped onto the standard LSP ``SemanticTokenTypes`` values so
the legend needs no custom entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence

#: Top-level block keywords -> LSP "keyword".
BLOCK_KEYWORDS = (
    "service|database|cache|queue|storage|network|secret|config"
    "|pipeline|environment|cluster"
)

#: Known type values (database/cache/queue engines) -> LSP "type".
_TYPE_VALUES = {
    "postgres", "mysql", "mariadb", "mongodb", "redis", "valkey",
    "memcached", "rabbitmq", "kafka", "nats",
}

_BLOCK_RE = re.compile(rf"\b({BLOCK_KEYWORDS})\b")
_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:[A-Za-z]+)?")
_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`')
_COMMENT_RE = re.compile(r"#.*$")


@dataclass(frozen=True)
class SemanticToken:
    """A single token: 0-based line, start column, length, LSP token type."""

    line: int
    start: int
    length: int
    token_type: str


def _emit(
    tokens: List[SemanticToken], line: int, start: int, text: str, type_: str
) -> None:
    if text:
        tokens.append(SemanticToken(line, start, len(text), type_))


def tokenize_source(source: str) -> List[SemanticToken]:
    """Tokenize ``source`` into a list of semantic tokens.

    Never raises; malformed input simply yields whatever tokens are recognizable.
    """
    tokens: List[SemanticToken] = []
    for i, raw_line in enumerate(source.splitlines()):
        # Split off a trailing comment once.
        line = raw_line
        comment_start = None
        m = _COMMENT_RE.search(line)
        if m:
            comment_start = m.start()
            line = line[: m.start()]

        # Whole-line comment (or blank after stripping).
        if comment_start is not None and not line.strip():
            _emit(tokens, i, comment_start, raw_line[comment_start:], "comment")
            continue

        _tokenize_line(tokens, i, line)
        if comment_start is not None:
            _emit(tokens, i, comment_start, raw_line[comment_start:], "comment")
    return tokens


def _tokenize_line(tokens: List[SemanticToken], line_no: int, line: str) -> None:
    pos = 0
    n = len(line)
    while pos < n:
        ch = line[pos]
        # Skip whitespace and punctuation.
        if ch.isspace() or ch in "{}[],:()":
            pos += 1
            continue

        # String literal.
        if ch in ('"', "`"):
            m = _STRING_RE.match(line, pos)
            if m:
                _emit(tokens, line_no, pos, m.group(0), "string")
                pos = m.end()
                continue
            pos += 1
            continue

        # Number.
        m = _NUMBER_RE.match(line, pos)
        if m and line[pos].isdigit():
            _emit(tokens, line_no, pos, m.group(0), "number")
            pos = m.end()
            continue

        # Identifier / keyword.
        m = _IDENT_RE.match(line, pos)
        if m:
            word = m.group(0)
            _emit(tokens, line_no, pos, word, _classify(word, line, pos))
            pos = m.end()
            continue

        pos += 1


def _classify(word: str, line: str, pos: int) -> str:
    """Classify an identifier given its surrounding line context."""
    if word in _TYPE_VALUES:
        return "type"
    # Field: `name:` at start of line (or after whitespace) -> property.
    if pos == 0 or line[pos - 1].isspace():
        rest = line[pos + len(word):].lstrip()
        if rest.startswith(":"):
            return "property"
    # Block keyword -> keyword.
    if _BLOCK_RE.fullmatch(word):
        return "keyword"
    # A name immediately after a block keyword -> variable (resource name).
    before = line[:pos].rstrip()
    kw = _BLOCK_RE.search(before)
    if kw is not None and kw.end() == len(before):
        return "variable"
    # Value reference (e.g. inside `depends: [...]`) -> variable.
    return "variable"


def encode_delta(tokens: Sequence[SemanticToken]) -> List[int]:
    """Encode tokens into the LSP semantic-tokens delta format.

    Each token becomes 5 ints relative to the previous one:
    [deltaLine, deltaStartChar, length, tokenTypeIndex, tokenModifiers(0)].
    """
    data: List[int] = []
    prev_line = 0
    prev_start = 0
    for t in tokens:
        # 0-based -> 1-based line index in the encoding, then subtract prev.
        line = t.line
        start = t.start
        delta_line = line - prev_line
        if delta_line == 0:
            delta_start = start - prev_start
        else:
            delta_start = start
        data.extend([delta_line, delta_start, t.length, _TYPE_INDEX[t.token_type], 0])
        prev_line = line
        prev_start = start if delta_line > 0 else start
    return data


#: Map our token-type strings to LSP SemanticTokenTypes indices (position in the
#: legend). Kept stable so the legend and encoder stay in sync.
TOKEN_TYPES = ["keyword", "type", "variable", "property", "string", "number", "comment"]
_TYPE_INDEX = {t: i for i, t in enumerate(TOKEN_TYPES)}
