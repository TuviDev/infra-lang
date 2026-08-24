"""Completeness: parser/lexer/expression/CLI edge cases."""

from __future__ import annotations

import pytest

from infra.analyzer.types import (
    AnyType,
    BoolType,
    DurationType,
    ErrorType,
    FloatType,
    IntType,
    ListType,
    MapType,
    NullType,
    OptionalType,
    PercentageType,
    ResourceValueType,
    StringType,
    UnionType,
    UnknownType,
    are_types_compatible,
    infer_literal_type,
    unify_types,
)
from infra.errors.exceptions import InfraLexError, InfraParseError
from infra.parser import Parser, parse, parse_expression

P = Parser()


class TestParseExpression:
    def test_single_expr(self):
        from infra.parser.ast_nodes import Literal

        assert parse_expression("42").value == 42

    def test_binary_expr(self):
        from infra.parser.ast_nodes import BinaryOp

        assert isinstance(parse_expression("1 + 2"), BinaryOp)


class TestParserErrors:
    def test_lex_error(self):
        with pytest.raises(InfraLexError):
            P.parse('service a { image: "unterminated }')

    def test_parse_error_location(self):
        with pytest.raises(InfraParseError) as e:
            P.parse('service a { image: ')
        assert e.value.location is not None

    def test_parse_error_expected(self):
        with pytest.raises(InfraParseError) as e:
            P.parse("service {")
        assert e.value.expected is not None


class TestTypeSystem:
    def test_primitives_str(self):
        assert str(StringType()) == "string"
        assert str(IntType()) == "int"
        assert str(FloatType()) == "float"
        assert str(BoolType()) == "bool"
        assert str(NullType()) == "null"
        assert str(DurationType()) == "duration"
        assert str(ResourceValueType()) == "resource"
        assert str(PercentageType()) == "percentage"
        assert str(AnyType()) == "any"
        assert str(UnknownType()) == "unknown"
        assert str(ErrorType()) == "error"

    def test_composite_str(self):
        assert str(ListType(StringType())) == "list[string]"
        assert str(MapType(StringType(), IntType())) == "map[string, int]"
        assert str(OptionalType(IntType())) == "int?"
        assert str(UnionType((StringType(), IntType()))) == "string | int"

    def test_int_assignable_from_float(self):
        assert IntType().is_assignable_from(FloatType())

    def test_list_assignable(self):
        assert ListType(StringType()).is_assignable_from(ListType(StringType()))
        assert not ListType(StringType()).is_assignable_from(ListType(IntType()))

    def test_union_assignable(self):
        u = UnionType((StringType(), IntType()))
        assert u.is_assignable_from(StringType())
        assert u.is_assignable_from(IntType())
        assert not u.is_assignable_from(BoolType())

    def test_optional_assignable(self):
        o = OptionalType(IntType())
        assert o.is_assignable_from(IntType())
        assert o.is_assignable_from(NullType())
        assert not o.is_assignable_from(StringType())

    def test_any_compatible_all(self):
        for t in [StringType(), IntType(), BoolType(), NullType()]:
            assert AnyType().is_compatible(t)

    def test_infer_literal(self):
        import infra.analyzer.types as T

        assert infer_literal_type("x") == T.STRING
        assert infer_literal_type(1) == T.INT
        assert infer_literal_type(1.0) == T.FLOAT
        assert infer_literal_type(True) == T.BOOL
        assert infer_literal_type(None) == T.NULL

    def test_are_types_compatible(self):
        assert are_types_compatible(StringType(), StringType())
        assert not are_types_compatible(StringType(), IntType())

    def test_unify(self):
        import infra.analyzer.types as T

        assert unify_types([]) == T.ANY
        assert isinstance(unify_types([IntType()]), IntType)
        assert unify_types([IntType(), FloatType()]) == T.FLOAT
        assert isinstance(unify_types([StringType(), IntType()]), UnionType)


class TestProgramAndImports:
    def test_program_imports_separate(self):
        prog = parse('import "./x.infra"\nservice a { image: "b" }')
        assert len(prog.imports) == 1
        assert len(prog.statements) >= 1

    def test_from_import(self):
        prog = parse('from "./x.infra" import A, B')
        assert prog.imports[0].names == ("A", "B")


class TestBOM:
    """UTF-8 BOM (added by Windows editors / PowerShell) must not crash parsing."""

    def test_parse_with_bom(self):
        source = '\ufeffservice api { image: "nginx:1.25" port 80 }'
        result = parse(source)
        assert result is not None
        assert len(result.statements) >= 1

    def test_parse_with_bom_middle_source_unaffected(self):
        # BOM stripping must only affect a leading \ufeff, never content.
        source = 'service api { image: "nginx:1.25" port 80 }'
        result = parse(source)
        assert result is not None

    def test_parse_file_with_bom(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "app.infra"
            bom_src = '\ufeffservice api { image: "nginx:1.25" port 80 }'
            p.write_text(bom_src, encoding="utf-8")
            from infra.parser import parse_file

            result = parse_file(p)
            assert result is not None


class TestCLISmoke:
    def test_version(self):
        from typer.testing import CliRunner

        from infra.cli.main import app

        result = CliRunner().invoke(app, ["--version"])
        assert "0.4.4" in result.output

    def test_help_lists_commands(self):
        from typer.testing import CliRunner

        from infra.cli.main import app

        result = CliRunner().invoke(app, ["--help"])
        for cmd in ["compile", "validate", "fmt", "repl", "init", "check", "graph", "docs", "diff"]:
            assert cmd in result.output
