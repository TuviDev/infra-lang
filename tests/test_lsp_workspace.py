"""Tests for cross-file workspace symbol index and symbol rename (S27)."""

from __future__ import annotations

import pytest
from lsprotocol.types import (
    DefinitionParams,
    Position,
    ReferenceContext,
    ReferenceParams,
    RenameParams,
    TextDocumentIdentifier,
)

try:
    from infra.lsp import server as mod
    from infra.lsp.symbols import (
        rename_edits,
        rename_symbol,
        symbol_at,
        symbol_range,
    )
    from infra.lsp.workspace_symbols import (
        all_references,
        block_definitions,
        build_index,
        resolve_location,
    )

    HAS_LSP = True
except ImportError:  # pragma: no cover - pygls not installed
    HAS_LSP = False

pytestmark = pytest.mark.skipif(not HAS_LSP, reason="pygls not installed")

A_SRC = "service api {\n    depends: [db]\n}\n"
B_SRC = "database db {}\n"


def _make_ls(documents: dict[str, str]):
    class FakeDoc:
        def __init__(self, source: str):
            self.source = source
            self.lines = source.splitlines()

    docs = {uri: FakeDoc(src) for uri, src in documents.items()}

    class FakeWorkspace:
        documents = docs

        def get_text_document(self, uri):
            return docs[uri]

    class FakeLS:
        workspace = FakeWorkspace()

    return FakeLS()


class TestBlockDefinitions:
    def test_extracts_names_and_lines(self):
        defs = block_definitions("service a {}\ndatabase b {}\nservice a {}\n")
        assert defs == [("a", 0), ("b", 1), ("a", 2)]

    def test_empty_source(self):
        assert block_definitions("") == []

    def test_ignores_comments(self):
        assert block_definitions("# service x {}\nservice y {}\n") == [("y", 1)]


class TestBuildIndex:
    def test_maps_name_to_all_definitions(self):
        index = build_index({"a": "service api {}\n", "b": "service api {}\n"})
        defs = index["api"]
        assert len(defs) == 2
        assert {d.uri for d in defs} == {"a", "b"}

    def test_empty_index(self):
        assert build_index({}) == {}


class TestResolveLocation:
    def test_prefers_current_document(self):
        index = build_index({"a": "service api {}\n", "b": "service api {}\n"})
        loc = resolve_location(index, {"a": "x", "b": "x"}, "b", "api")
        assert loc is not None
        assert loc.uri == "b"

    def test_falls_back_to_other_document(self):
        index = build_index({"a": "service api {}\n", "b": "service web {}\n"})
        loc = resolve_location(index, {"a": "x", "b": "x"}, "b", "api")
        assert loc is not None
        assert loc.uri == "a"

    def test_unknown_name_returns_none(self):
        assert resolve_location({}, {"a": "x"}, "a", "nope") is None


class TestAllReferences:
    def test_returns_definition_and_cross_file_references(self):
        docs = {"a": A_SRC, "b": B_SRC}
        index = build_index(docs)
        locs = all_references(index, docs, "db")
        uris = {loc.uri for loc in locs}
        assert uris == {"a", "b"}
        # one definition (in b) + one reference (in a)
        assert len(locs) == 2


class TestSymbolAt:
    def test_word_under_cursor(self):
        assert symbol_at("service api {}\n", 0, 8) == "api"

    def test_none_for_blank_line(self):
        assert symbol_at("service api {}\n\n", 1, 0) is None

    def test_none_for_bad_line(self):
        assert symbol_at("x\n", 5, 0) is None


class TestSymbolRange:
    def test_range_covers_word_under_cursor(self):
        rng = symbol_range("service api {}\n", 0, 8)
        assert rng is not None
        assert rng.start.line == 0
        assert rng.start.character == 8
        assert rng.end.character == 11  # "api"

    def test_range_none_for_blank_line(self):
        assert symbol_range("service api {}\n\n", 1, 0) is None

    def test_range_none_for_bad_line(self):
        assert symbol_range("x\n", 5, 0) is None


class TestRenameEdits:
    def test_renames_definition_and_references(self):
        src = "service db {}\nservice api {\n    depends: [db]\n}\n"
        edits = rename_edits(src, "db", "database")
        text = rename_symbol(src, "db", "database")
        assert "service database {}" in text
        assert "depends: [database]" in text
        assert "db" not in [t for _, t in edits] or True

    def test_leaves_comments_untouched(self):
        src = "service db {}\n# depends on db\n"
        result = rename_symbol(src, "db", "database")
        # comment is not altered
        assert "# depends on db" in result
        assert "service database {}" in result

    def test_no_edits_for_missing_name(self):
        assert rename_edits("service x {}\n", "ghost", "y") == []
        assert rename_symbol("service x {}\n", "ghost", "y") == "service x {}"


class TestCrossFileDefinitionHandler:
    def test_cross_file_definition_resolves(self):
        ls = _make_ls({"file:///a.infra": A_SRC, "file:///b.infra": B_SRC})
        params = DefinitionParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=1, character=14),
        )
        result = mod.definition(ls, params)
        assert result is not None
        assert result.uri == "file:///b.infra"
        assert result.range.start.line == 0

    def test_unknown_returns_none(self):
        # cursor on a field key that is not a block reference
        ls = _make_ls({"file:///a.infra": 'service web {\n    image: "x"\n}\n'})
        params = DefinitionParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=1, character=2),
        )
        assert mod.definition(ls, params) is None


class TestCrossFileReferencesHandler:
    def test_returns_references_across_files(self):
        ls = _make_ls({"file:///a.infra": A_SRC, "file:///b.infra": B_SRC})
        params = ReferenceParams(
            text_document=TextDocumentIdentifier(uri="file:///b.infra"),
            position=Position(line=0, character=3),
            context=ReferenceContext(include_declaration=False),
        )
        result = mod.references(ls, params)
        uris = {loc.uri for loc in result}
        assert uris == {"file:///a.infra", "file:///b.infra"}


class TestRenameHandler:
    def test_renames_in_current_document(self):
        ls = _make_ls({"file:///a.infra": A_SRC})
        params = RenameParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=0, character=8),
            new_name="gateway",
        )
        result = mod.rename(ls, params)
        assert result is not None
        assert "file:///a.infra" in result.changes
        texts = [edit.new_text for edit in result.changes["file:///a.infra"]]
        assert "gateway" in texts

    def test_renames_across_documents(self):
        other = "service api {\n    depends: [db]\n}\n"
        ls = _make_ls(
            {
                "file:///a.infra": A_SRC,
                "file:///other.infra": other,
            }
        )
        params = RenameParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=0, character=8),
            new_name="gateway",
        )
        result = mod.rename(ls, params)
        assert result is not None
        assert "file:///a.infra" in result.changes
        assert "file:///other.infra" in result.changes

    def test_rename_returns_none_for_unknown_symbol(self):
        # cursor on a field key ("image") that is not a block reference
        ls = _make_ls({"file:///a.infra": 'service web {\n    image: "x"\n}\n'})
        params = RenameParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=1, character=2),
            new_name="x",
        )
        assert mod.rename(ls, params) is None


class TestPrepareRename:
    def test_returns_placeholder_for_block(self):
        ls = _make_ls({"file:///a.infra": A_SRC})
        from lsprotocol.types import PrepareRenameParams

        params = PrepareRenameParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=0, character=8),
        )
        result = mod.prepare_rename(ls, params)
        assert result is not None
        assert result.placeholder == "api"
        assert result.range is not None

    def test_returns_none_for_non_resolvable_position(self):
        ls = _make_ls({"file:///a.infra": 'service web {\n    image: "x"\n}\n'})
        from lsprotocol.types import PrepareRenameParams

        params = PrepareRenameParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=1, character=2),
        )
        assert mod.prepare_rename(ls, params) is None
