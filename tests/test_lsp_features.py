"""Tests for the S39 LSP feature block: diagnostics enrichment, signature
help, document highlight, and folding ranges."""

from __future__ import annotations

import pytest
from lsprotocol.types import (
    DefinitionParams,
    DiagnosticSeverity,
    Position,
    RenameParams,
    TextDocumentIdentifier,
)

try:
    from infra.lsp import server as mod
    from infra.lsp.folding import folding_ranges
    from infra.lsp.signature import signature_help_at
    from infra.lsp.symbols import highlight_ranges

    HAS_LSP = True
except ImportError:  # pragma: no cover - pygls not installed
    HAS_LSP = False

pytestmark = pytest.mark.skipif(not HAS_LSP, reason="pygls not installed")


# --------------------------------------------------------------------------- #
# PRIORITY 1 — Diagnostics enhancement
# --------------------------------------------------------------------------- #


class TestDiagnosticsEnhancement:
    def test_severity_mapping(self):
        # parse error -> Error
        diags = mod._diagnose("service {\n", "t.infra")
        assert any(d.severity == DiagnosticSeverity.Error and d.code == "PARSE"
                   for d in diags)
        # replicas 0 -> Error (E011)
        diags = mod._diagnose('service api { image: "x" replicas: 0 }', "t.infra")
        assert any(d.severity == DiagnosticSeverity.Error and d.code == "E011"
                   for d in diags)
        # security hardcoded secret -> Error (SEC001)
        diags = mod._diagnose(
            'service api { image: "x" env { PASSWORD: "hunter2" } }', "t.infra"
        )
        assert any(d.severity == DiagnosticSeverity.Error and d.code == "SEC001"
                   for d in diags)
        # reliability (missing memory limit) -> Warning
        diags = mod._diagnose(
            'service api { image: "x" replicas: 5 resources { requests { cpu: 100m } } }',
            "t.infra",
        )
        assert any(d.code == "REL003" and d.severity == DiagnosticSeverity.Warning
                   for d in diags)

    def test_code_and_source_present(self):
        diags = mod._diagnose(
            'service api { image: "x" env { PASSWORD: "hunter2" } }', "t.infra"
        )
        assert diags
        for d in diags:
            assert d.source == "infra-lang"
            assert d.code
            assert d.code_description is not None
            href = d.code_description.href
            # Canonical base lives in src/infra/lsp/server.py (_DOCS_BASE);
            # assert suffix path + code anchor, never a hardcoded owner name
            # (stale trees kept asserting the old kakukpl.github.io prefix).
            assert href.startswith(mod._DOCS_BASE)
            assert "/infra-lang/language_spec/" in href
            assert href.rsplit("#", 1)[-1] == d.code.lower()

    def test_related_info_for_duplicate_name(self):
        src = 'service api { image: "x" }\nservice api { image: "y" }\n'
        diags = mod._diagnose(src, "t.infra")
        dup = next((d for d in diags if d.code == "E002"), None)
        assert dup is not None
        assert dup.related_information, "duplicate name should have related info"
        # the related info points to the earlier definition on line 0
        assert any(ri.location.range.start.line == 0
                   for ri in dup.related_information)

    def test_no_related_info_for_non_duplicate(self):
        diags = mod._diagnose(
            'service api { image: "x" replicas: 0 }', "t.infra"
        )
        e011 = next((d for d in diags if d.code == "E011"), None)
        assert e011 is not None
        assert not e011.related_information

    def test_existing_diagnostics_unbroken(self):
        # a valid service produces no error diagnostics
        diags = mod._diagnose(
            'service api {\n    image: "nginx:1.25"\n    port 8080\n}\n',
            "t.infra",
        )
        errors = [d for d in diags if d.severity == DiagnosticSeverity.Error]
        assert len(errors) == 0


# --------------------------------------------------------------------------- #
# PRIORITY 2 — Signature help
# --------------------------------------------------------------------------- #


class TestSignatureHelp:
    def test_service_block_fields(self):
        src = "service api {\n    |\n}\n"
        sh = signature_help_at(src, 1, 0)
        assert sh is not None
        labels = [s.label for s in sh.signatures]
        assert "image" in labels
        assert "port" in labels
        assert "replicas" in labels
        assert any("resources" in l for l in labels)

    def test_database_block_fields(self):
        src = "database db {\n    |\n}\n"
        sh = signature_help_at(src, 1, 0)
        assert sh is not None
        labels = [s.label for s in sh.signatures]
        assert "type" in labels
        assert "storage" in labels
        assert any(l.startswith("backup") for l in labels)

    def test_invalid_position_returns_none(self):
        # cursor outside any block -> None, no crash
        assert signature_help_at("service api {}\n", 0, 5) is None
        assert signature_help_at("", 0, 0) is None

    def test_used_fields_marked(self):
        src = 'service api {\n    image: "x"\n    |\n}\n'
        sh = signature_help_at(src, 2, 0)
        assert sh is not None
        image = next(s for s in sh.signatures if s.label.startswith("image"))
        assert "(set)" in image.label

    def test_trigger_inside_block_after_open(self):
        # cursor on the opening brace line also detects the block
        sh = signature_help_at("service api {|", 0, 13)
        assert sh is None or any("image" in s.label for s in sh.signatures)


# --------------------------------------------------------------------------- #
# PRIORITY 3 — Document highlight
# --------------------------------------------------------------------------- #


class TestDocumentHighlight:
    def test_definition_is_write(self):
        src = "service api {}\nservice web {\n    depends: [api]\n}\n"
        name, ranges = highlight_ranges(src, 0, 8)
        assert name == "api"
        kinds = [k for _, k in ranges]
        assert "write" in kinds  # definition line

    def test_reference_is_read(self):
        src = "service api {}\nservice web {\n    depends: [api]\n}\n"
        name, ranges = highlight_ranges(src, 2, 14)
        assert name == "api"
        # find the depends reference
        assert any(r.start.line == 2 and k == "read" for r, k in ranges)

    def test_word_boundary(self):
        # api must not highlight api-2
        src = "service api {}\nservice api-2 {}\n"
        name, ranges = highlight_ranges(src, 0, 8)
        assert name == "api"
        # only line 0 (the real api definition); api-2 untouched
        assert all(r.start.line == 0 for r, k in ranges)

    def test_nonexistent_returns_empty(self):
        src = 'service api {\n    image: "x"\n}\n'
        name, ranges = highlight_ranges(src, 1, 2)  # cursor on "image" key
        assert name is None
        assert ranges == []

    def test_rename_still_works_after_highlight(self):
        # highlight shares the word-boundary logic with rename; rename must
        # still respect it (api -> x must not touch api-2).
        from infra.lsp.symbols import rename_symbol

        src = "service api {}\nservice api-2 {}\n"
        out = rename_symbol(src, "api", "x")
        assert "service x {}" in out
        assert "service api-2 {}" in out


# --------------------------------------------------------------------------- #
# PRIORITY 4 — Folding ranges
# --------------------------------------------------------------------------- #


class TestFoldingRanges:
    def test_top_level_service_block(self):
        src = "service api {\n    image: \"x\"\n}\n"
        ranges = folding_ranges(src)
        assert any(r.start_line == 0 and r.end_line == 2 for r in ranges)

    def test_nested_resources_block(self):
        src = (
            "service api {\n"
            "    resources {\n"
            "        requests { cpu: 100m }\n"
            "        limits { cpu: 200m }\n"
            "    }\n"
            "}\n"
        )
        ranges = folding_ranges(src)
        # nested resources block folds (lines 1-4)
        assert any(r.start_line == 1 and r.end_line == 4 for r in ranges)

    def test_multiple_blocks(self):
        src = "service a {\n    image: \"x\"\n}\nservice b {\n    image: \"y\"\n}\n"
        ranges = folding_ranges(src)
        assert any(r.start_line == 0 for r in ranges)
        assert any(r.start_line == 3 for r in ranges)

    def test_malformed_no_crash(self):
        for src in ["", "service {", "}", "{", "service api {", "%%%"]:
            assert isinstance(folding_ranges(src), list)

    def test_empty_blocks_not_folded(self):
        # single-line `{}` blocks have nothing to collapse
        src = "service a {}\nservice b {}\n"
        assert folding_ranges(src) == []

    def test_comment_run_folded(self):
        src = "# one\n# two\n# three\nservice api {}\n"
        ranges = folding_ranges(src)
        assert any(r.kind == "comment" and r.start_line == 0 and r.end_line == 2
                   for r in ranges)


# --------------------------------------------------------------------------- #
# Backward-compat guards for existing handlers
# --------------------------------------------------------------------------- #


class TestExistingHandlersUnbroken:
    def test_definition_handler_still_works(self):
        class FakeDoc:
            source = "service api {}\n"
            lines = source.splitlines()

        class FakeWorkspace:
            def get_text_document(self, uri):
                return FakeDoc()

        class FakeLS:
            workspace = FakeWorkspace()

        params = DefinitionParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=0, character=8),
        )
        assert mod.definition(FakeLS(), params) is not None

    def test_rename_handler_still_works(self):
        from lsprotocol.types import RenameParams

        class FakeDoc:
            source = "service api {\n    depends: [db]\n}\n"
            lines = source.splitlines()

        class FakeWorkspace:
            def get_text_document(self, uri):
                return FakeDoc()

        class FakeLS:
            workspace = FakeWorkspace()

        params = RenameParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=0, character=8),
            new_name="gateway",
        )
        mod.workspace_index.clear()
        try:
            result = mod.rename(FakeLS(), params)
            assert result is not None
            assert "file:///a.infra" in result.changes
        finally:
            mod.workspace_index.clear()


class TestSignatureHelpEdgeBranches:
    def test_unbalanced_close_resets_depth(self):
        # a stray `}` earlier must not crash field detection
        src = '}\nservice api {\n    image: "x"\n    |\n}\n'
        sh = signature_help_at(src, 3, 0)
        assert sh is not None
        labels = [s.label for s in sh.signatures]
        assert any(l.startswith("image") for l in labels)

    def test_braces_without_block_keyword(self):
        # braces on a non-block line -> _current_block returns None
        src = "const x = {\n    |\n}\n"
        # _current_block should not raise and returns None (no block keyword)
        from infra.lsp.signature import _current_block
        assert _current_block(src.splitlines(), 1) is None
        assert signature_help_at(src, 1, 0) is None

    def test_nested_close_before_cursor(self):
        # closing brace inside a sub-block before the cursor line
        src = "service api {\n    resources {\n    }\n    |\n}\n"
        sh = signature_help_at(src, 3, 0)
        assert sh is not None
        assert sh.signatures  # still resolves to service block


class TestFeatureHandlers:
    """Exercise the LSP server handlers directly (fake workspace)."""

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

    def test_signature_help_handler(self):
        from lsprotocol.types import SignatureHelpParams

        ls = self._ls("service api {\n    |\n}\n")
        params = SignatureHelpParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=1, character=0),
        )
        result = mod.signature_help(ls, params)
        assert result is not None
        assert result.signatures
        assert any("image" in s.label for s in result.signatures)

    def test_signature_help_handler_none_outside_block(self):
        from lsprotocol.types import SignatureHelpParams

        ls = self._ls("service api {}\n")
        params = SignatureHelpParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=0, character=5),
        )
        assert mod.signature_help(ls, params) is None

    def test_document_highlight_handler(self):
        from lsprotocol.types import DocumentHighlightParams, DocumentHighlightKind

        src = "service api {}\nservice web {\n    depends: [api]\n}\n"
        ls = self._ls(src)
        params = DocumentHighlightParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=0, character=8),
        )
        result = mod.document_highlight(ls, params)
        assert result
        # definition is Write
        assert any(h.kind == DocumentHighlightKind.Write for h in result)

    def test_document_highlight_handler_empty(self):
        from lsprotocol.types import DocumentHighlightParams

        ls = self._ls('service api {\n    image: "x"\n}\n')
        params = DocumentHighlightParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra"),
            position=Position(line=1, character=2),
        )
        assert mod.document_highlight(ls, params) == []

    def test_folding_handler(self):
        from lsprotocol.types import FoldingRangeParams

        ls = self._ls("service api {\n    image: \"x\"\n}\n")
        params = FoldingRangeParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra")
        )
        result = mod.folding_range(ls, params)
        assert any(r.start_line == 0 for r in result)

    def test_folding_handler_empty(self):
        from lsprotocol.types import FoldingRangeParams

        ls = self._ls("")
        params = FoldingRangeParams(
            text_document=TextDocumentIdentifier(uri="file:///a.infra")
        )
        assert mod.folding_range(ls, params) == []
