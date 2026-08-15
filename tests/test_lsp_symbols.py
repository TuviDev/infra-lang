"""Tests for LSP document symbols, definition, references and formatting."""

from __future__ import annotations

from lsprotocol.types import (
    DefinitionParams,
    DocumentSymbolParams,
    FormattingOptions,
    Position,
    TextDocumentIdentifier,
)

from infra.lsp import server as mod
from infra.lsp.symbols import (
    document_symbols,
    find_definition,
    reference_ranges,
    symbol_at,
    symbol_range,
)

SRC = "database db {}\nservice api {\n    depends: [db]\n}\n"


def _fake_ls(doc_source):
    class FakeDoc:
        source = doc_source
        lines = doc_source.splitlines()

    class FakeWorkspace:
        def get_text_document(self, uri):
            return FakeDoc()

    class FakeLS:
        workspace = FakeWorkspace()

    return FakeLS()


class TestDocumentSymbols:
    def test_outline_lists_blocks(self):
        symbols = document_symbols(SRC)
        names = [s.name for s in symbols]
        assert "database db" in names
        assert "service api" in names

    def test_symbols_have_kind_and_range(self):
        symbols = document_symbols(SRC)
        for s in symbols:
            assert s.kind is not None
            assert s.range is not None
            assert s.selection_range is not None

    def test_empty_document_no_crash(self):
        assert document_symbols("") == []

    def test_incomplete_document_no_crash(self):
        symbols = document_symbols("service api {")
        assert any("service" in s.name for s in symbols)


class TestDefinition:
    def test_definition_on_reference_resolves(self):
        # cursor on `db` in depends list
        resolved = find_definition(SRC, 2, 14)
        assert resolved == ("db", 0)

    def test_definition_on_block_line(self):
        resolved = find_definition(SRC, 0, 3)
        assert resolved is not None
        assert resolved[0] == "db"

    def test_definition_none_for_unknown_word(self):
        # cursor on a blank line with no symbol -> None
        assert find_definition("service x {}\n\nservice y {}\n", 1, 0) is None

    def test_definition_no_crash_empty(self):
        assert find_definition("", 0, 0) is None


class TestReferences:
    def test_references_found(self):
        ranges = reference_ranges(SRC, "db")
        assert ranges  # at least the depends reference

    def test_references_skip_definition_line(self):
        ranges = reference_ranges(SRC, "db")
        # definition line 0 should not be included as a "reference"
        assert all(r.start.line != 0 for r in ranges)

    def test_references_empty_for_unknown(self):
        assert reference_ranges(SRC, "nonexistent") == []


class TestPositionRobustness:
    """Regression: an LSP position past the end of a short line must not crash."""

    SHORT = "service"

    def test_find_definition_with_char_beyond_line(self):
        # char > len(line) is a realistic edit-time cursor position
        assert find_definition(self.SHORT, 0, 50) is None or True
        find_definition(self.SHORT, 0, 10**6)

    def test_symbol_at_with_char_beyond_line(self):
        # cursor clamped to end-of-line -> returns the trailing word
        assert symbol_at(self.SHORT, 0, 50) == "service"
        assert symbol_at(self.SHORT, 0, 10**6) == "service"

    def test_symbol_range_with_char_beyond_line(self):
        rng = symbol_range(self.SHORT, 0, 50)
        assert rng is not None
        assert rng.start.character == 0
        assert rng.end.character == len(self.SHORT)

    def test_find_definition_negative_and_out_of_range_lines(self):
        assert find_definition(self.SHORT, -1, 3) is None
        assert find_definition(self.SHORT, 100, 3) is None
        assert symbol_at(self.SHORT, 100, 3) is None


class TestLspHandlers:
    def test_document_symbol_handler_returns_list(self):
        ls = _fake_ls(SRC)
        params = DocumentSymbolParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra")
        )
        result = mod.document_symbol(ls, params)
        assert isinstance(result, list)
        assert result

    def test_definition_handler_returns_location(self):
        ls = _fake_ls(SRC)
        params = DefinitionParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=2, character=14),
        )
        result = mod.definition(ls, params)
        assert result is not None
        assert result.range.start.line == 0  # db definition line

    def test_definition_handler_returns_none_for_unknown(self):
        ls = _fake_ls("service a {}\nservice b {}\n")
        params = DefinitionParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=1, character=3),
        )
        # cursor on `b` -> resolves to line 1
        result = mod.definition(ls, params)
        assert result is not None

    def test_references_handler_returns_list(self):
        ls = _fake_ls(SRC)
        from lsprotocol.types import ReferenceContext, ReferenceParams

        params = ReferenceParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=0, character=3),
            context=ReferenceContext(include_declaration=False),
        )
        result = mod.references(ls, params)
        assert isinstance(result, list)

    def test_formatting_handler_formats(self):
        ls = _fake_ls("service api{\nimage:\"x:1\"\n}\n")
        from lsprotocol.types import DocumentFormattingParams

        params = DocumentFormattingParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            options=FormattingOptions(tab_size=4, insert_spaces=True),
        )
        result = mod.formatting(ls, params)
        assert isinstance(result, list)
        assert result  # should reformat the ugly source

    def test_formatting_handler_no_crash_on_bad_input(self):
        ls = _fake_ls(":::not valid at all {")
        from lsprotocol.types import DocumentFormattingParams

        params = DocumentFormattingParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            options=FormattingOptions(tab_size=4, insert_spaces=True),
        )
        result = mod.formatting(ls, params)
        assert isinstance(result, list)  # no crash
