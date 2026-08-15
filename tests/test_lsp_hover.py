"""Tests for LSP hover documentation (expanded in Session 20)."""

from __future__ import annotations

import pytest
from lsprotocol.types import HoverParams, Position, TextDocumentIdentifier

from infra.lsp import server as mod


def _fake_ls(doc_lines):
    class FakeDoc:
        lines = doc_lines

    class FakeWorkspace:
        def get_text_document(self, uri):
            return FakeDoc()

    class FakeLS:
        workspace = FakeWorkspace()

    return FakeLS()


class TestHoverTopLevel:
    def test_hover_for_service_block(self):
        ls = _fake_ls(["service api {"])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=0, character=2),
        )
        hover = mod.hover(ls, params)
        assert hover is not None
        assert "service" in hover.contents.value

    def test_hover_for_database_block(self):
        ls = _fake_ls(["database db {"])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=0, character=3),
        )
        hover = mod.hover(ls, params)
        assert hover is not None
        assert "database" in hover.contents.value


class TestHoverField:
    def test_hover_for_image_field(self):
        ls = _fake_ls(['    image: "nginx:1.25"'])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=0, character=7),
        )
        hover = mod.hover(ls, params)
        assert hover is not None
        assert "image" in hover.contents.value

    def test_hover_for_strategy_field(self):
        ls = _fake_ls(["    strategy: rolling"])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=0, character=7),
        )
        hover = mod.hover(ls, params)
        assert hover is not None
        assert "strategy" in hover.contents.value


class TestHoverEdgeCases:
    def test_hover_returns_none_for_unknown_word(self):
        ls = _fake_ls(["  some-random-word"])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=0, character=4),
        )
        assert mod.hover(ls, params) is None

    def test_hover_no_crash_on_empty_doc(self):
        ls = _fake_ls([])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=0, character=0),
        )
        # must not raise
        mod.hover(ls, params)

    def test_hover_no_crash_out_of_range_position(self):
        ls = _fake_ls(["service"])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=5, character=50),
        )
        mod.hover(ls, params)  # must not raise


class TestHoverCoverage:
    def test_key_service_fields_have_docs(self):
        for field in ["image", "replicas", "port", "health", "resources"]:
            assert field in mod.FIELD_DOCS, f"missing hover doc for {field}"

    def test_key_database_fields_have_docs(self):
        for field in ["type", "version", "storage", "ssl", "backup"]:
            assert field in mod.FIELD_DOCS, f"missing hover doc for {field}"

    def test_popular_elements_have_docs(self):
        for kw in ["cache", "secret", "pipeline", "quotas", "namespace"]:
            assert kw in mod.FIELD_DOCS, f"missing hover doc for {kw}"
