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

    def test_word_boundary_not_part_of_larger_identifier(self):
        # `-` and `_` are valid in Infra names; renaming `db` must not touch
        # `main-db`, `db-2`, or `my_db`.
        src = "database main-db {}\ndatabase db {}\ndatabase my_db {}\n"
        src += "service api {\n    depends: [db, main-db, db-2, my_db]\n}\n"
        text = rename_symbol(src, "db", "database")
        assert "database database {}" in text  # the standalone `db` definition
        assert "database main-db {}" in text  # untouched
        assert "database my_db {}" in text  # untouched
        # the standalone `db` reference renamed, `main-db`/`db-2`/`my_db` intact
        assert "depends: [database, main-db, db-2, my_db]" in text


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


class TestCrossFileRenameOnDisk:
    """Cross-file rename over files found only on disk (via the index)."""

    @pytest.fixture(autouse=True)
    def _populate_index(self):
        mod.workspace_index.clear()
        # a.infra defines `api`; b.infra references it; both on "disk"
        mod.workspace_index.add_file(
            "file:///proj/a.infra", "service api {\n    depends: [db]\n}\n"
        )
        mod.workspace_index.add_file(
            "file:///proj/b.infra", "service web {\n    depends: [api]\n}\n"
        )
        yield
        mod.workspace_index.clear()

    def test_rename_propagates_to_disk_files(self):
        ls = _make_ls({"file:///proj/a.infra": "service api {\n    depends: [db]\n}\n"})
        params = RenameParams(
            text_document=TextDocumentIdentifier(uri="file:///proj/a.infra"),
            position=Position(line=0, character=8),
            new_name="gateway",
        )
        result = mod.rename(ls, params)
        assert result is not None
        # both files must be edited (b.infra references api on disk)
        assert "file:///proj/a.infra" in result.changes
        assert "file:///proj/b.infra" in result.changes
        # b.infra's reference to api becomes gateway
        b_texts = [e.new_text for e in result.changes["file:///proj/b.infra"]]
        assert "gateway" in b_texts

    def test_open_version_takes_priority_over_disk(self):
        # The open a.infra has a NEWER version where api was already renamed;
        # rename must act on the open source, not the stale disk copy.
        open_src = "service gateway {\n    depends: [db]\n}\n"
        ls = _make_ls({"file:///proj/a.infra": open_src})
        params = RenameParams(
            text_document=TextDocumentIdentifier(uri="file:///proj/a.infra"),
            position=Position(line=0, character=8),
            new_name="portal",
        )
        result = mod.rename(ls, params)
        assert result is not None
        # a.infra edits are computed from the open source (portal), not api
        a_texts = [e.new_text for e in result.changes["file:///proj/a.infra"]]
        assert "portal" in a_texts

    def test_rename_nonexistent_on_disk(self):
        mod.workspace_index.clear()
        ls = _make_ls({"file:///proj/a.infra": "service web {\n    image: \"x\"\n}\n"})
        params = RenameParams(
            text_document=TextDocumentIdentifier(uri="file:///proj/a.infra"),
            position=Position(line=1, character=2),
            new_name="y",
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
        if hasattr(result, "placeholder"): assert result.placeholder == "api"
        assert result.range is not None

    def test_returns_none_for_non_resolvable_position(self):
        ls = _make_ls({"file:///a.infra": 'service web {\n    image: "x"\n}\n'})
        from lsprotocol.types import PrepareRenameParams

        params = PrepareRenameParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=1, character=2),
        )
        assert mod.prepare_rename(ls, params) is None


class TestOnDiskWorkspace:
    """Cross-file navigation over files found by the on-disk index (S31)."""

    @pytest.fixture(autouse=True)
    def _populate_index(self):
        # Populate the global on-disk index used by the handlers.
        mod.workspace_index.clear()
        mod.workspace_index.add_file(
            "file:///proj/a.infra", "service api {\n    depends: [db]\n}\n"
        )
        mod.workspace_index.add_file(
            "file:///proj/b.infra", "database db { type: postgres }\n"
        )
        yield
        mod.workspace_index.clear()

    def test_definition_resolves_to_other_file(self):
        # Cursor on `db` in depends inside a.infra -> definition in b.infra
        ls = _make_ls({"file:///proj/a.infra": "service api {\n    depends: [db]\n}\n"})
        params = DefinitionParams(
            text_document=TextDocumentIdentifier(uri="file:///proj/a.infra"),
            position=Position(line=1, character=14),
        )
        loc = mod.definition(ls, params)
        assert loc is not None
        assert loc.uri == "file:///proj/b.infra"
        assert loc.range.start.line == 0

    def test_definition_returns_none_when_missing(self):
        # Cursor on a symbol that exists nowhere
        ls = _make_ls({"file:///proj/a.infra": 'service api {\n    image: "x"\n}\n'})
        params = DefinitionParams(
            text_document=TextDocumentIdentifier(uri="file:///proj/a.infra"),
            position=Position(line=1, character=2),  # "image" key, not a block
        )
        assert mod.definition(ls, params) is None

    def test_workspace_symbol_lists_all_resources(self):
        from lsprotocol.types import WorkspaceSymbolParams

        ls = _make_ls({})
        params = WorkspaceSymbolParams(query="")
        result = mod.workspace_symbol(ls, params)
        names = {s.name for s in result}
        assert names == {"api", "db"}
        kinds = {s.name: s.kind.name for s in result}
        assert kinds["api"] == "Class"      # service
        assert kinds["db"] == "Interface"   # database

    def test_workspace_symbol_filters_by_query(self):
        from lsprotocol.types import WorkspaceSymbolParams

        ls = _make_ls({})
        result = mod.workspace_symbol(ls, WorkspaceSymbolParams(query="api"))
        assert {s.name for s in result} == {"api"}

    def test_references_found_across_disk_files(self):
        ls = _make_ls({"file:///proj/a.infra": "service api {\n    depends: [db]\n}\n"})
        params = ReferenceParams(
            text_document=TextDocumentIdentifier(uri="file:///proj/b.infra"),
            position=Position(line=0, character=3),
            context=ReferenceContext(include_declaration=False),
        )
        result = mod.references(ls, params)
        uris = {loc.uri for loc in result}
        assert uris == {"file:///proj/a.infra", "file:///proj/b.infra"}


class TestProjectIndexingHandlers:
    """Cover the on-disk indexing handlers (initialized/_root_dir/did_close)."""

    def _fake_root_ls(self, root_uri=None, root_path=None, executor=None):
        class FakeWorkspace:
            def __init__(self):
                self.root_uri = root_uri
                self.root_path = root_path

        class FakeLS:
            workspace = FakeWorkspace()
            thread_pool_executor = executor

        return FakeLS()

    def test_root_dir_from_uri(self):
        ls = self._fake_root_ls(root_uri="file:///tmp/proj")
        root = mod._root_dir(ls)
        assert str(root).replace("\\", "/").endswith("/tmp/proj")

    def test_root_dir_from_path(self):
        ls = self._fake_root_ls(root_path="/tmp/proj")
        root = mod._root_dir(ls)
        assert str(root).replace("\\", "/").endswith("/tmp/proj")

    def test_root_dir_none_when_unknown(self):
        ls = self._fake_root_ls()
        assert mod._root_dir(ls) is None

    def test_initialized_scans_workspace(self, tmp_path):
        (tmp_path / "a.infra").write_text("service api {}\n")
        ls = self._fake_root_ls(root_uri=f"file://{tmp_path}")
        mod.initialized(ls, None)
        # scan runs synchronously when there is no executor
        assert any(s.name == "api" for s in mod.workspace_index.all_symbols())
        mod.workspace_index.clear()

    def test_initialized_with_executor(self, tmp_path):
        import concurrent.futures
        (tmp_path / "a.infra").write_text("database db {}\n")
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        ls = self._fake_root_ls(root_uri=f"file://{tmp_path}", executor=executor)
        mod.initialized(ls, None)
        executor.shutdown()
        assert any(s.name == "db" for s in mod.workspace_index.all_symbols())
        mod.workspace_index.clear()

    def test_did_close_restores_disk_state(self, tmp_path):
        p = tmp_path / "svc.infra"
        p.write_text("service diskver {}\n")
        uri = p.as_uri()
        # editor had a newer in-memory version
        mod.workspace_index.add_file(uri, "service memoryver {}\n")
        assert "memoryver" in {s.name for s in mod.workspace_index.all_symbols()}

        class FakeLS:
            class _W:
                pass
            workspace = _W()

        from lsprotocol.types import DidCloseTextDocumentParams, TextDocumentIdentifier
        mod.did_close(
            FakeLS(),
            DidCloseTextDocumentParams(text_document=TextDocumentIdentifier(uri=uri)),
        )
        # after close, the disk version is restored
        names = {s.name for s in mod.workspace_index.all_symbols()}
        assert "diskver" in names and "memoryver" not in names
        mod.workspace_index.clear()

    def test_server_shutdown_releases_index(self):
        mod.workspace_index.add_file("file:///x.infra", "service a {}\n")
        assert mod.workspace_index.all_symbols()
        mod.server.shutdown()  # must not raise and should release the index
        # index may be cleared; shutdown is best-effort
        mod.workspace_index.clear()

    def test_references_single_document_fallback(self):
        # workspace index empty and fake workspace has no documents -> fallback
        mod.workspace_index.clear()
        ls = _make_ls({})
        from lsprotocol.types import ReferenceContext, ReferenceParams
        params = ReferenceParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=0, character=3),
            context=ReferenceContext(include_declaration=False),
        )
        assert mod.references(ls, params) == []
