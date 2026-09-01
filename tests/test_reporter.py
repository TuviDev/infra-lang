"""Error reporter tests."""

from __future__ import annotations

from infra.errors.exceptions import (
    InfraCompileError,
    InfraLexError,
    InfraParseError,
)
from infra.errors.reporter import (
    ErrorReporter,
    get_source_line,
    highlight_range,
)


class TestReporterMethods:
    def test_report_lex_error(self):
        err = InfraLexError(
            "Unexpected char",
            source="x",
            line=1,
            column=2,
            file="t.infra",
            unexpected_char="@",
        )
        out = ErrorReporter(use_color=False).report_lex_error(err, "x")
        assert "1" in out and "2" in out

    def test_report_parse_error(self):
        err = InfraParseError(
            "Unexpected",
            source="x",
            line=3,
            column=5,
            file="t.infra",
            expected=["A", "B"],
            got="C",
        )
        out = ErrorReporter(use_color=False).report_parse_error(err, "x")
        assert "3" in out

    def test_report_compile_error(self):
        err = InfraCompileError("bad", backend="kubernetes", line=1, column=1)
        out = ErrorReporter(use_color=False).report_compile_error(err, "x")
        assert "kubernetes" in out

    def test_report_error_dispatch(self):
        lex = InfraLexError("c", source="x", line=1, column=1)
        assert "Unexpected" in ErrorReporter(use_color=False).report_error(lex, "x")

    def test_format_multiple_errors(self):
        errs = [
            InfraParseError("a", source="x", line=1, column=1),
            InfraParseError("b", source="x", line=2, column=1),
        ]
        out = ErrorReporter().format_multiple_errors(errs, "x", max=1)
        assert "more error" in out

    def test_get_source_line(self):
        assert get_source_line("a\nb\nc", 2) == "b"
        assert get_source_line("a", 5) is None

    def test_highlight_range(self):
        assert isinstance(highlight_range("hello", 1, 3), str)

    def test_report_semantic_with_warnings(self):
        from infra import parse, validate

        result = validate(parse('let unused = "x"\nservice a { image: "nginx:1.0" }'))
        out = ErrorReporter().report_semantic_errors(
            result.errors, result.warnings, "src"
        )
        assert isinstance(out, str)


class TestBaseHelpers:
    def test_evaluate_resource_docker(self):
        from infra.backends.base import evaluate_resource
        from infra.parser.ast_nodes import ResourceValue

        assert evaluate_resource(ResourceValue(128, "Mi"), "docker") == "134217728"

    def test_snake_to_camel(self):
        from infra.backends.base import BaseYAMLBackend

        b = BaseYAMLBackend()
        assert b._snake_to_camel("read_only_root") == "readOnlyRoot"

    def test_clean_none(self):
        from infra.backends.base import BaseYAMLBackend

        b = BaseYAMLBackend()
        data = {"a": None, "b": {"c": None, "d": 1}, "e": [], "f": [{"g": None}]}
        cleaned = b._clean_none(data)
        assert "a" not in cleaned
        assert "c" not in cleaned["b"]
        assert "e" not in cleaned
        assert cleaned["f"] == [{}]

    def test_evaluate_duration_float(self):
        from infra.backends.base import evaluate_duration
        from infra.parser.ast_nodes import Duration

        assert evaluate_duration(Duration(1.5, "s")) == "1.5s"
