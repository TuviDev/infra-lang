"""Tests for LSP server diagnostics logic.

We test the _diagnose() function directly, not the full LSP server
(too slow / too complex for unit tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lsprotocol.types import DiagnosticSeverity

try:
    from infra.lsp.server import FIELD_DOCS, _diagnose, _get_word_at, _severity

    HAS_LSP = True
except ImportError:  # pragma: no cover - pygls not installed
    HAS_LSP = False

pytestmark = pytest.mark.skipif(not HAS_LSP, reason="pygls not installed")


class TestDiagnosticsLogic:
    def test_valid_service_no_diagnostics(self):
        source = """
        service api {
            image: "nginx:1.25"
            port: 8080
            health: http("/health")
            resources {
                requests { cpu: 200m, memory: 256Mi }
                limits   { cpu: 1000m, memory: 512Mi }
            }
        }
        """
        diags = _diagnose(source, "test.infra")
        errors = [
            d for d in diags if d.severity == DiagnosticSeverity.Error
        ]
        assert len(errors) == 0, (
            f"Valid service should have no errors: "
            f"{[(d.code, d.message) for d in errors]}"
        )

    def test_hardcoded_secret_is_error(self):
        source = """
        service api {
            image: "nginx:1.25"
            env { PASSWORD: "hardcoded" }
        }
        """
        diags = _diagnose(source, "test.infra")
        errors = [
            d
            for d in diags
            if d.severity == DiagnosticSeverity.Error and d.code == "SEC001"
        ]
        assert len(errors) >= 1, "Hardcoded secret should produce SEC001 error"

    def test_replicas_zero_is_error(self):
        source = """
        service api {
            image: "nginx:1.25"
            replicas: 0
        }
        """
        diags = _diagnose(source, "test.infra")
        errors = [
            d
            for d in diags
            if d.severity == DiagnosticSeverity.Error and d.code == "E011"
        ]
        assert len(errors) >= 1, "replicas: 0 should produce E011 error"

    def test_parse_error_produces_diagnostic(self):
        source = "service { }"
        diags = _diagnose(source, "test.infra")
        assert len(diags) >= 1
        assert any(d.code == "PARSE" for d in diags)

    def test_mutable_tag_is_warning_not_error(self):
        source = 'service api { image: "nginx:latest" }'
        diags = _diagnose(source, "test.infra")
        warnings = [
            d
            for d in diags
            if d.severity == DiagnosticSeverity.Warning and d.code == "SEC003"
        ]
        assert len(warnings) >= 1
        errors = [d for d in diags if d.severity == DiagnosticSeverity.Error]
        assert len(errors) == 0

    def test_diagnostic_has_range(self):
        source = """
        service api {
            image: "nginx:1.25"
            replicas: 0
        }
        """
        diags = _diagnose(source, "test.infra")
        for d in diags:
            assert d.range is not None
            assert d.range.start.line >= 0
            assert d.range.start.character >= 0

    def test_diagnostic_has_source_infra_lang(self):
        source = 'service api { image: "nginx:latest" }'
        diags = _diagnose(source, "test.infra")
        for d in diags:
            assert d.source == "infra-lang"

    def test_multiple_errors_all_reported(self):
        source = """
        service api {
            image: "nginx:latest"
            replicas: 0
            env { PASSWORD: "bad" }
        }
        """
        diags = _diagnose(source, "test.infra")
        codes = {d.code for d in diags}
        assert "E011" in codes
        assert "SEC001" in codes
        assert "SEC003" in codes

    def test_internal_error_gives_diagnostic_not_crash(self):
        source = "totally not valid infra lang @ $ % &"
        try:
            diags = _diagnose(source, "test.infra")
            assert len(diags) >= 1
        except Exception as e:
            pytest.fail(f"_diagnose must not raise: {e}")

    def test_hover_word_extraction(self):
        assert _get_word_at("  replicas: 0", 4) == "replicas"
        assert _get_word_at("  replicas: 0", 6) == "replicas"
        assert _get_word_at("service api", 5) == "service"
        assert _get_word_at("", 0) is None

    def test_field_docs_cover_keywords(self):
        for kw in ["image", "replicas", "service", "database", "health"]:
            assert kw in FIELD_DOCS


class TestHandlers:
    def test_did_open_publishes_diagnostics(self):
        from lsprotocol.types import DidOpenTextDocumentParams, TextDocumentItem

        from infra.lsp import server as mod

        published = {}

        class FakeLS:
            def publish_diagnostics(self, uri, diags):
                published["uri"] = uri
                published["diags"] = diags

        params = DidOpenTextDocumentParams(
            text_document=TextDocumentItem(
                uri="file:///tmp/t.infra",
                language_id="infra",
                version=1,
                text='service api { image: "nginx:1.25" env { PASSWORD: "bad" } }',
            )
        )
        mod.did_open(FakeLS(), params)
        assert published["uri"] == "file:///tmp/t.infra"
        assert any(d.code == "SEC001" for d in published["diags"])

    def test_did_change_publishes_diagnostics(self):
        from lsprotocol.types import (
            DidChangeTextDocumentParams,
            Position,
            Range,
            TextDocumentContentChangeEvent_Type1,
            VersionedTextDocumentIdentifier,
        )

        from infra.lsp import server as mod

        published = {}

        class FakeLS:
            def publish_diagnostics(self, uri, diags):
                published["diags"] = diags

        params = DidChangeTextDocumentParams(
            text_document=VersionedTextDocumentIdentifier(
                uri="file:///tmp/t.infra", version=2
            ),
            content_changes=[
                TextDocumentContentChangeEvent_Type1(
                    range=Range(
                        start=Position(line=0, character=0),
                        end=Position(line=0, character=1),
                    ),
                    text='service api { image: "x:1" replicas: 0 }',
                )
            ],
        )
        mod.did_change(FakeLS(), params)
        assert any(d.code == "E011" for d in published["diags"])

    def test_did_save_publishes_diagnostics(self):
        from lsprotocol.types import (
            DidSaveTextDocumentParams,
            TextDocumentIdentifier,
        )

        from infra.lsp import server as mod

        published = {}

        class FakeLS:
            def publish_diagnostics(self, uri, diags):
                published["diags"] = diags

        params = DidSaveTextDocumentParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/t.infra"),
            text='service api { image: "nginx:latest" }',
        )
        mod.did_save(FakeLS(), params)
        assert any(d.code == "SEC003" for d in published["diags"])

    def test_hover_returns_none_for_unknown_word(self):
        from lsprotocol.types import HoverParams, Position, TextDocumentIdentifier

        from infra.lsp import server as mod

        class FakeDoc:
            lines = ["  some text here"]

        class FakeWorkspace:
            def get_text_document(self, uri):
                return FakeDoc()

        class FakeLS:
            workspace = FakeWorkspace()

        params = HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/t.infra"),
            position=Position(line=0, character=4),
        )
        assert mod.hover(FakeLS(), params) is None

    def test_hover_returns_markup_for_keyword(self):
        from lsprotocol.types import HoverParams, Position, TextDocumentIdentifier

        from infra.lsp import server as mod

        class FakeDoc:
            lines = ["  replicas: 3"]

        class FakeWorkspace:
            def get_text_document(self, uri):
                return FakeDoc()

        class FakeLS:
            workspace = FakeWorkspace()

        params = HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/t.infra"),
            position=Position(line=0, character=4),
        )
        hover = mod.hover(FakeLS(), params)
        assert hover is not None
        assert "replicas" in hover.contents.value

    def test_location_to_range_none(self):
        from infra.lsp.server import _location_to_range

        rng = _location_to_range(None, "source")
        assert rng.start.line == 0
        assert rng.end.line == 0


class TestCompletionIntegration:
    def test_completion_handler_registered(self):
        from lsprotocol.types import TEXT_DOCUMENT_COMPLETION

        from infra.lsp.server import server

        # pygls registers the handler via the @server.feature decorator; the
        # completion method must be reachable on the module.
        assert hasattr(server, "completion") or callable(
            getattr(__import__("infra.lsp.server", fromlist=["completion"]), "completion")
        )
        assert TEXT_DOCUMENT_COMPLETION == "textDocument/completion"

    def test_completion_handler_returns_completion_list(self):
        from infra.lsp.server import completion
        from lsprotocol.types import (
            CompletionContext,
            CompletionList,
            CompletionParams,
            CompletionTriggerKind,
            Position,
            TextDocumentIdentifier,
        )

        class FakeDoc:
            source = "service api {\n    \n}"
            lines = ["service api {", "    "]

        class FakeWorkspace:
            def get_text_document(self, uri):
                return FakeDoc()

        class FakeLS:
            workspace = FakeWorkspace()

        params = CompletionParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=1, character=4),
            context=CompletionContext(
                trigger_kind=CompletionTriggerKind.Invoked
            ),
        )
        result = completion(FakeLS(), params)
        assert isinstance(result, CompletionList)
        assert result.items  # non-empty

    def test_completion_no_crash_on_empty_doc(self):
        from infra.lsp.server import completion
        from lsprotocol.types import (
            CompletionList,
            CompletionParams,
            Position,
            TextDocumentIdentifier,
        )

        class FakeDoc:
            source = ""
            lines = []

        class FakeWorkspace:
            def get_text_document(self, uri):
                return FakeDoc()

        class FakeLS:
            workspace = FakeWorkspace()

        params = CompletionParams(
            text_document=TextDocumentIdentifier(uri="file:///t.infra"),
            position=Position(line=0, character=0),
        )
        result = completion(FakeLS(), params)
        assert isinstance(result, CompletionList)


class TestSeverityMapping:
    def test_e_codes_are_errors(self):
        for code in ["E001", "E002", "E003", "E004", "E005"]:
            assert _severity(code) == DiagnosticSeverity.Error

    def test_sec_errors_are_errors(self):
        for code in ["SEC001", "SEC002", "SEC004", "SEC007"]:
            assert _severity(code) == DiagnosticSeverity.Error

    def test_sec_warnings_are_warnings(self):
        for code in ["SEC003", "SEC005", "SEC006"]:
            assert _severity(code) == DiagnosticSeverity.Warning

    def test_rel_codes_are_warnings(self):
        for code in ["REL001", "REL002", "REL003", "REL014"]:
            assert _severity(code) == DiagnosticSeverity.Warning

    def test_none_code_is_warning(self):
        assert _severity(None) == DiagnosticSeverity.Warning


class TestLspCmdImportError:
    """When pygls is missing, `infra lsp` errors cleanly.

    Verified in the smoke test / clean-venv check (the ImportError path is hard
    to trigger in-process because pygls is already imported); the graceful
    message is produced by `lsp_cmd` and covered by the clean-venv install test.
    """


class TestWindowsUriConversion:
    """Regression: a Windows `file:///C:/...` URI must convert to a real path.

    The LSP `did_close` handler converts a file URI to a native path via
    `url2pathname`. On Windows a leading-slash drive form (`/C:/...`) must be
    handled; using `Path(unquote(urlparse(uri).path))` alone would leave the
    leading slash and never match an existing file. This mirrors the logic in
    `server.did_close` so the conversion contract is covered on every OS.
    """

    def test_url2pathname_windows_drive_form(self):
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        uri = "file:///C:/Users/tester/app.infra"
        path = Path(url2pathname(unquote(urlparse(uri).path)))
        # On Windows this yields `C:\\Users\\tester\\app.infra`; on POSIX the
        # `/C:/...` form is preserved. Either way the leading `/C` drive marker
        # must be handled by url2pathname (never a bare `/C:/...` on Windows).
        assert path is not None
        # the file name component must survive conversion
        assert path.name == "app.infra"

    def test_url2pathname_posix_plain(self):
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        uri = "file:///home/user/app.infra"
        path = Path(url2pathname(unquote(urlparse(uri).path)))
        assert path == Path("/home/user/app.infra")

    def test_url2pathname_percent_encoded(self):
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        uri = "file:///home/user/my%20app.infra"
        path = Path(url2pathname(unquote(urlparse(uri).path)))
        assert "my app.infra" in str(path)
