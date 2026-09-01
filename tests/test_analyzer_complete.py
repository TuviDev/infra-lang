"""Completeness: diff engine, reporter, exception hierarchy, linter internals."""

from __future__ import annotations

import json

from infra import parse, validate
from infra.diff.engine import FieldChange, InfraDiff
from infra.errors.exceptions import (
    InfraCompileError,
    InfraError,
    InfraLexError,
    InfraParseError,
    InfraRuntimeError,
    InfraSemanticError,
    ValidationError,
)
from infra.errors.reporter import ErrorReporter


class TestExceptionHierarchy:
    def test_lex_error(self):
        e = InfraLexError("msg", source="x", line=1, column=2, unexpected_char="@")
        assert e.location is not None
        assert e.unexpected_char == "@"
        assert "1:2" in str(e.location)
        d = e.to_dict()
        assert d["type"] == "InfraLexError"

    def test_parse_error(self):
        e = InfraParseError("msg", expected=["A"], got="B")
        assert e.expected == ["A"] and e.got == "B"

    def test_compile_error(self):
        e = InfraCompileError("bad", backend="k8s", reason="x")
        assert e.backend == "k8s" and e.reason == "x"

    def test_runtime_error(self):
        e = InfraRuntimeError("bad", expression="expr", reason="r")
        assert e.expression == "expr" and e.reason == "r"

    def test_semantic_error(self):
        e = InfraSemanticError([ValidationError(message="m", code="E1")])
        assert len(e.errors) == 1

    def test_validation_error_to_dict(self):
        ve = ValidationError(message="m", code="E1", hint="h")
        d = ve.to_dict()
        assert d["code"] == "E1" and d["hint"] == "h"

    def test_infra_error_is_base(self):
        assert issubclass(InfraLexError, InfraError)


class TestReporter:
    def test_format_as_json(self):
        result = validate(parse('service a { image: "x" replicas: 0 }'))
        data = json.loads(ErrorReporter().format_as_json(result))
        assert "valid" in data and data["valid"] is False
        assert len(data["errors"]) > 0

    def test_report_lex(self):
        e = InfraLexError("c", source="x", line=1, column=1, unexpected_char="@")
        out = ErrorReporter(use_color=False).report_lex_error(e, "x")
        assert "Unexpected" in out

    def test_report_parse(self):
        e = InfraParseError("u", source="x", line=1, column=1, expected=["A"], got="B")
        out = ErrorReporter(use_color=False).report_parse_error(e, "x")
        assert "A" in out

    def test_suggest_similar(self):
        from infra.errors.reporter import suggest_similar

        assert suggest_similar("postgress", ["postgres"]) == "postgres"
        assert suggest_similar("xyz", ["postgres"]) is None
        assert suggest_similar("x", []) is None

    def test_get_source_line(self):
        from infra.errors.reporter import get_source_line

        assert get_source_line("a\nb", 1) == "a"
        assert get_source_line("a", 5) is None

    def test_highlight_range(self):
        from infra.errors.reporter import highlight_range

        assert isinstance(highlight_range("hello", 1, 3), str)


class TestDiffEngine:
    def _diff(self, a, b):
        return InfraDiff().diff(parse(a), parse(b))

    def test_added_removed_changed(self):
        r = self._diff(
            'service a { image: "x:1" }',
            'service a { image: "x:2" }\nservice b { image: "y" }',
        )
        assert any(i.name == "b" for i in r.added)
        assert len(r.changed) == 1

    def test_changed_fields(self):
        r = self._diff(
            'service a { image: "x:1" replicas: 2 }',
            'service a { image: "x:2" replicas: 5 }',
        )
        c = r.changed[0]
        assert any("image" in ch.field_path for ch in c.changes)
        assert any("replicas" in ch.field_path for ch in c.changes)

    def test_diff_result_format(self):
        r = self._diff('service a { image: "x:1" }', 'service a { image: "x:2" }')
        assert "a" in r.format(color=False)
        assert "has_changes" in r.format_json()

    def test_field_change_format(self):
        fc = FieldChange("replicas", 2, 5)
        assert "2" in fc.format() and "5" in fc.format()

    def test_changed_item_format(self):
        from infra.diff.engine import ChangedItem

        ci = ChangedItem(
            kind="service", name="a", changes=[FieldChange("replicas", 2, 5)]
        )
        assert "change(s)" in ci.format(color=False)


class TestLinterInternals:
    def test_reliability_checker(self):
        from infra.analyzer.reliability import ReliabilityChecker

        warnings = ReliabilityChecker().check(
            parse('service a { image: "x:1" replicas: 5 }')
        )
        assert any(w.code == "REL001" for w in warnings)

    def test_security_checker(self):
        from infra.analyzer.security import SecurityChecker

        findings = SecurityChecker().check(
            parse('service a { image: "x:latest" env { PASSWORD: "bad" } }')
        )
        assert any(getattr(f, "code", "") == "SEC001" for f in findings)


class TestExpressionRecursion:
    """Cover `_check_expression` recursive branches for various expr node types."""

    def _err_codes(self, src):
        from infra import parse, validate

        return {e.code for e in validate(parse(src)).errors}

    def test_binary_op_expression(self):
        # 1 + 2 references nothing undefined -> no E001
        assert "E001" not in self._err_codes('let a = 1 + 2\nservice s { image: "x" }')

    def test_unary_op_expression(self):
        assert "E001" not in self._err_codes('let a = !false\nservice s { image: "x" }')

    def test_list_expression(self):
        assert "E001" not in self._err_codes(
            'let a = [1,2,3]\nservice s { image: "x" }'
        )

    def test_call_expression(self):
        assert "E001" not in self._err_codes(
            'let a = upper("x")\nservice s { image: "x" }'
        )

    def test_if_expr_expression(self):
        assert "E001" not in self._err_codes(
            'let cond = true\nservice s { image: "x" env { A: if cond then "a" else '
            '"b" '
            '} }'
        )

    def test_undefined_identifier_flagged(self):
        assert "E001" in self._err_codes(
            'let a = missing_var\nservice s { image: "x" }'
        )

    def test_call_with_undefined_arg_flagged(self):
        assert "E001" in self._err_codes(
            'let a = upper(nope)\nservice s { image: "x" }'
        )
