"""Containment tests for the pygls 2.x LSP migration (v0.5.0).

Verifies the server module works against the pygls 2.x architecture:
``LanguageServer`` lives in ``pygls.lsp.server``, diagnostics are published
through ``protocol.notify(TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS, ...)``,
``prepareRename`` returns a ``PrepareRenamePlaceholder`` (the renamed
``PrepareRenameResult_Type1``), the workspace document store is read via
``Workspace.text_documents``, and completion/hover know the new
v0.4.5/v0.5.0 keywords (``depends_on``, ``secret_store``, ``resource``).
"""

from __future__ import annotations

from importlib import metadata

import lsprotocol.types as t
import pytest
from lsprotocol.types import (
    Position,
    PrepareRenameParams,
    PrepareRenameResult,
    Range,
    TextDocumentIdentifier,
)

from infra.lsp.completion import completions_at
from infra.lsp.server import _publish, _workspace_documents, server

try:
    from lsprotocol.types import PrepareRenamePlaceholder
except ImportError:
    # lsprotocol 2023.x (pygls 1.3.1) predates this type; these architecture
    # tests are skipped on that stack (see pytestmark below), but the module
    # must still COLLECT cleanly so `pytest tests/` reports zero errors.
    PrepareRenamePlaceholder = None

# These tests pin the pygls 2.x architecture (import locations, lsprotocol
# 2025 type shapes). On a pygls 1.3.x / lsprotocol 2023.x install they are
# not meaningful, so the whole module skips instead of failing.
pytestmark = pytest.mark.skipif(
    int(metadata.version("pygls").split(".")[0]) < 2,
    reason="pygls 2.x architecture tests; pygls 1.3.x uses legacy paths",
)


class TestPygls2Architecture:
    def test_pygls_2_is_installed(self):
        major = int(metadata.version("pygls").split(".")[0])
        assert major >= 2

    def test_language_server_import_location(self):
        from pygls.lsp.server import LanguageServer

        assert isinstance(server, LanguageServer)
        assert server.name == "infra-lang"

    def test_no_legacy_import_paths(self):
        # `pygls.server` is a low-level JSON-RPC module in 2.x — LanguageServer
        # must NOT be importable from there anymore
        import pygls.server as low_level

        assert not hasattr(low_level, "LanguageServer")

    def test_lsprotocol_2025_rename_contract(self):
        assert hasattr(t, "PrepareRenameResult")
        assert not hasattr(t, "PrepareRenameResult_Type1")
        names = {
            getattr(a, "__forward_arg__", getattr(a, "__name__", str(a)))
            for a in t.PrepareRenameResult.__args__  # type: ignore[attr-defined]
        }
        assert "PrepareRenamePlaceholder" in names

    def test_prepare_rename_handler_returns_placeholder(self):
        from infra.lsp.server import prepare_rename

        class FakeLS:
            class workspace:  # noqa: N801,D106 - shadows ls.workspace
                @staticmethod
                def get_text_document(uri):  # noqa: ANN001, ANN202
                    class Doc:
                        source = (
                            'service api { image: "x" }\nservice db { image: "y" }\n'
                        )
                        lines = source.splitlines()

                    return Doc()

        params = PrepareRenameParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/x.infra"),
            position=Position(line=0, character=9),
        )
        result = prepare_rename(FakeLS(), params)
        assert isinstance(result, PrepareRenamePlaceholder)
        assert result.placeholder == "api"


class TestDiagnosticsPublishPath:
    def test_publish_uses_protocol_notify_on_pygls2(self):
        sent: list[tuple[str, object]] = []

        class FakeServer:
            """A pygls-2.x-shaped server: no `publish_diagnostics` helper."""

            class protocol:  # noqa: N801,D106 - shadows ls.protocol
                @staticmethod
                def notify(method, params=None):  # noqa: ANN001
                    sent.append((method, params))

        _publish(
            FakeServer(),  # type: ignore[arg-type]
            "file:///tmp/t.infra",
            'service api { image: "x" replicas: 0 }',
        )
        assert sent, "expected a TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS notify"
        method, params = sent[0]
        assert method == t.TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS
        assert isinstance(params, t.PublishDiagnosticsParams)
        assert params.uri == "file:///tmp/t.infra"
        assert len(params.diagnostics) > 0

    def test_publish_falls_back_to_legacy_api(self):
        class FakeLS:
            def __init__(self):
                self.published: list[tuple[str, object]] = []

            def publish_diagnostics(self, uri, diags):  # noqa: ANN001
                self.published.append((uri, diags))

        fake = FakeLS()
        _publish(fake, "file:///tmp/t.infra", 'service api { image: "x" }')  # type: ignore[arg-type]
        assert len(fake.published) == 1
        uri, _ = fake.published[0]
        assert uri == "file:///tmp/t.infra"

    def test_workspace_documents_prefers_text_documents(self):
        class FakeLS:
            def __init__(self):
                from pygls.workspace import Workspace

                self.workspace = Workspace("/tmp")

        fake = FakeLS()
        fake.workspace.put_text_document(
            t.TextDocumentItem(
                uri="file:///tmp/a.infra",
                language_id="infra",
                version=1,
                text='service api { image: "x" }',
            )
        )
        docs = _workspace_documents(fake)  # type: ignore[arg-type]
        assert "file:///tmp/a.infra" in docs
        assert "service api" in docs["file:///tmp/a.infra"]


class TestCompletionForNewKeywords:
    def test_top_level_suggests_secret_store_and_resource(self):
        labels = {i.label for i in completions_at("", 0, 0)}
        assert "secret_store" in labels
        assert "resource" in labels

    def test_service_block_suggests_depends_on(self):
        src = "service api {\n    \n}\n"
        labels = {i.label for i in completions_at(src, 1, 4)}
        assert "depends_on" in labels

    def test_depends_on_value_suggests_document_symbols(self):
        src = (
            'service db { image: "pg" }\n'
            "service api {\n"
            "    depends_on: [\n"
            "}\n"
        )
        items = completions_at(src, 2, len("    depends_on: ["))
        labels = {i.label for i in items}
        assert "db" in labels

    def test_secret_store_block_fields(self):
        src = 'secret_store "vs" {\n    \n}\n'
        labels = {i.label for i in completions_at(src, 1, 4)}
        assert "provider" in labels
        assert "address" in labels
        assert "path" in labels

    def test_secret_store_provider_value_hints(self):
        src = 'secret_store "vs" {\n    provider: \n}\n'
        items = completions_at(src, 1, len("    provider: "))
        labels = {i.label for i in items}
        assert "vault" in labels
        assert "aws" in labels
        assert "gcp" in labels

    def test_secret_block_suggests_store(self):
        src = "secret s {\n    \n}\n"
        labels = {i.label for i in completions_at(src, 1, 4)}
        assert "store" in labels

    def test_resource_block_fields(self):
        src = 'resource "crd" "x" {\n    \n}\n'
        labels = {i.label for i in completions_at(src, 1, 4)}
        assert "api_version" in labels
        assert "kind" in labels
        assert "spec" in labels


class TestHoverForNewKeywords:
    def test_depends_on_doc(self):
        from infra.lsp.server import FIELD_DOCS

        assert "depends_on" in FIELD_DOCS

    def test_secret_store_doc(self):
        from infra.lsp.server import FIELD_DOCS

        assert "secret_store" in FIELD_DOCS

    def test_store_doc(self):
        from infra.lsp.server import FIELD_DOCS

        assert "store" in FIELD_DOCS

    def test_resource_doc(self):
        from infra.lsp.server import FIELD_DOCS

        assert "resource" in FIELD_DOCS

    def test_range_position_types_unchanged(self):
        rng = Range(
            start=Position(line=0, character=0),
            end=Position(line=0, character=1),
        )
        assert rng.start.line == 0
        assert PrepareRenameResult is not None
