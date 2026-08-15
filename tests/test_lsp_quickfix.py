"""Tests for LSP code actions (quick fixes)."""

from __future__ import annotations

from lsprotocol.types import (
    CodeActionKind,
    Diagnostic,
    DiagnosticSeverity,
    Position,
    Range,
)

from infra.lsp.quickfix import quick_fixes
from infra.lsp.server import code_action

URI = "file:///t.infra"


def _diag(code: str, line: int = 0, col: int = 0) -> Diagnostic:
    return Diagnostic(
        range=Range(
            start=Position(line=line, character=col),
            end=Position(line=line, character=col + 1),
        ),
        message=f"{code} message",
        severity=DiagnosticSeverity.Error,
        code=code,
    )


class TestQuickFixEngine:
    def test_e011_replicas_zero_offers_fix(self):
        src = 'service s { image: "x:1" replicas: 0 }'
        actions = quick_fixes(URI, src, [_diag("E011")])
        assert len(actions) == 1
        assert actions[0].kind == CodeActionKind.QuickFix
        edit = actions[0].edit.changes[URI][0]
        assert edit.new_text == "1"
        assert "replicas: 1" in (
            src[: edit.range.start.character]
            + edit.new_text
            + src[edit.range.end.character :]
        )

    def test_e012_port_out_of_range_offers_fix(self):
        src = 'service s { image: "x:1" port: 70000 }'
        actions = quick_fixes(URI, src, [_diag("E012")])
        assert len(actions) == 1
        edit = actions[0].edit.changes[URI][0]
        assert edit.new_text == "8080"

    def test_no_action_for_unknown_code(self):
        src = 'service s { image: "x:1" }'
        assert quick_fixes(URI, src, [_diag("SEC001")]) == []

    def test_no_action_when_no_matching_value(self):
        # diagnostic present but no replicas: field on the line
        src = "const x = 1"
        assert quick_fixes(URI, src, [_diag("E011")]) == []

    def test_no_action_for_valid_value(self):
        # E011 diag but the value is already 1 (not in KNOWN_BAD)
        src = 'service s { image: "x:1" replicas: 1 }'
        assert quick_fixes(URI, src, [_diag("E011")]) == []

    def test_empty_source_no_crash(self):
        assert quick_fixes(URI, "", [_diag("E011")]) == []


class TestCodeActionHandler:
    def _fake_ls(self, doc_source):
        class FakeDoc:
            source = doc_source
            lines = doc_source.splitlines()

        class FakeWorkspace:
            def get_text_document(self, uri):
                return FakeDoc()

        class FakeLS:
            workspace = FakeWorkspace()

        return FakeLS()

    def test_handler_returns_actions(self):
        from lsprotocol.types import CodeActionContext, CodeActionParams

        src = 'service s { image: "x:1" replicas: 0 }'
        ls = self._fake_ls(src)
        params = CodeActionParams(
            text_document=__import__(
                "lsprotocol.types", fromlist=["TextDocumentIdentifier"]
            ).TextDocumentIdentifier(uri=URI),
            range=Range(
                start=Position(line=0, character=0),
                end=Position(line=0, character=1),
            ),
            context=CodeActionContext(diagnostics=[_diag("E011")]),
        )
        result = code_action(ls, params)
        assert isinstance(result, list)
        assert result
        assert result[0].kind == CodeActionKind.QuickFix

    def test_handler_empty_for_no_diagnostics(self):
        from lsprotocol.types import CodeActionContext, CodeActionParams

        src = 'service s { image: "x:1" }'
        ls = self._fake_ls(src)
        params = CodeActionParams(
            text_document=__import__(
                "lsprotocol.types", fromlist=["TextDocumentIdentifier"]
            ).TextDocumentIdentifier(uri=URI),
            range=Range(
                start=Position(line=0, character=0),
                end=Position(line=0, character=1),
            ),
            context=CodeActionContext(diagnostics=[]),
        )
        assert code_action(ls, params) == []
