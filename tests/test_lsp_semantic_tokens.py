"""Tests for LSP semantic tokens generation and delta encoding."""

from __future__ import annotations

import pytest

try:
    from infra.lsp import server as mod
    from infra.lsp.semantic_tokens import (
        TOKEN_TYPES,
        encode_delta,
        tokenize_source,
    )

    HAS_LSP = True
except ImportError:  # pragma: no cover - pygls not installed
    HAS_LSP = False

pytestmark = pytest.mark.skipif(not HAS_LSP, reason="pygls not installed")


def _types(src: str):
    """Return the ordered token types (by line:start) for a source."""
    toks = sorted(tokenize_source(src), key=lambda t: (t.line, t.start))
    return [(t.line, t.start, t.token_type) for t in toks]


class TestTokenize:
    def test_block_keyword_and_name(self):
        toks = _types("service api {}\n")
        # keyword at line 0 col 0, resource name "api" as variable
        assert (0, 0, "keyword") in toks
        assert (0, 8, "variable") in toks

    def test_field_is_property(self):
        toks = _types('service api {\n    image: "nginx:1.25"\n}\n')
        assert (1, 4, "property") in toks  # image:
        assert (1, 11, "string") in toks  # "nginx:1.25"

    def test_number(self):
        toks = _types("service api {\n    replicas: 3\n}\n")
        assert (1, 14, "number") in toks  # 3

    def test_type_value(self):
        toks = _types("database db {\n    type: postgres\n}\n")
        assert (1, 10, "type") in toks  # postgres

    def test_comment_tokenized(self):
        toks = _types("# a comment\nservice api {}\n")
        assert (0, 0, "comment") in toks

    def test_trailing_comment(self):
        toks = _types("service api {  # trailing\n}\n")
        assert any(t[0] == 0 and t[2] == "comment" for t in toks)

    def test_depends_values_are_variables(self):
        toks = _types("service api {\n    depends: [db, main-db]\n}\n")
        assert (1, 14, "variable") in toks  # db
        assert (1, 18, "variable") in toks  # main-db

    def test_malformed_does_not_crash(self):
        # half-typed / broken input must still produce tokens, not raise
        for src in ["", "service {", ":", ":::", "port 0:0", 'image: "', "%%%", "😀"]:
            tokens = tokenize_source(src)  # must not raise
            assert isinstance(tokens, list)


class TestDeltaEncoding:
    def test_delta_reconstructs_positions(self):
        src = 'service api {\n    image: "x:1"\n}\n'
        tokens = sorted(tokenize_source(src), key=lambda t: (t.line, t.start))
        data = encode_delta(tokens)
        # every token = 5 ints
        assert len(data) % 5 == 0
        # reconstruct positions and compare
        line = 0
        start = 0
        for i in range(0, len(data), 5):
            dline, dstart, length, tidx, mods = data[i : i + 5]
            line += dline
            start = dstart if dline > 0 else start + dstart
            orig = tokens[i // 5]
            assert line == orig.line
            assert start == orig.start
            assert length == orig.length
            assert tidx == TOKEN_TYPES.index(orig.token_type)

    def test_multi_line_delta_encodes_relative_starts(self):
        src = "service a {}\ndatabase b {}\n"
        tokens = sorted(tokenize_source(src), key=lambda t: (t.line, t.start))
        data = encode_delta(tokens)
        # Line 0 has two tokens (service, a); the first token of line 1
        # (database) is at index 10 and must carry a delta-line of 1.
        assert data[10] == 1


class TestSemanticTokensHandler:
    def _ls(self, source):
        class FakeDoc:
            def __init__(self):
                self.source = source
                self.lines = source.splitlines()

        class FakeWorkspace:
            def get_text_document(self, uri):
                return FakeDoc()

        class FakeLS:
            workspace = FakeWorkspace()

        return FakeLS()

    def test_handler_returns_semantic_tokens(self):
        from lsprotocol.types import SemanticTokensParams, TextDocumentIdentifier

        ls = self._ls("service api {\n    port 8080\n}\n")
        params = SemanticTokensParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra")
        )
        result = mod.semantic_tokens_full(ls, params)
        assert result is not None
        assert isinstance(result.data, list)
        assert len(result.data) >= 5  # at least one token
        assert len(result.data) % 5 == 0

    def test_handler_malformed_no_crash(self):
        from lsprotocol.types import SemanticTokensParams, TextDocumentIdentifier

        for src in ["", ":::", "service {", 'image: "']:
            ls = self._ls(src)
            params = SemanticTokensParams(
                text_document=TextDocumentIdentifier(uri="file:///a.infra")
            )
            result = mod.semantic_tokens_full(ls, params)
            assert isinstance(result.data, list)

    def test_registered_legend_matches_token_types(self):
        # The handler's legend must advertise every token type we emit.
        assert TOKEN_TYPES == list(TOKEN_TYPES)
        assert len(TOKEN_TYPES) == len(set(TOKEN_TYPES))
        required = {
            "keyword",
            "type",
            "variable",
            "property",
            "string",
            "number",
            "comment",
        }
        assert required.issubset(set(TOKEN_TYPES))
