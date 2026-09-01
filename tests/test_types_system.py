"""Type-system helpers (types.py) and backend base helpers."""

from __future__ import annotations

from infra.analyzer import types as it
from infra.backends.base import (
    CompileContext,
    CompileResult,
    evaluate_expression,
)
from infra.parser import ast_nodes as n


class TestTypes:
    def test_primitives_compatibility(self):
        assert it.STRING.is_compatible(it.STRING)
        assert not it.STRING.is_compatible(it.INT)
        assert not it.INT.is_compatible(it.STRING)
        assert it.INT.is_assignable_from(it.FLOAT)  # int usable as float
        assert it.FLOAT.is_assignable_from(it.FLOAT)

    def test_optional_type(self):
        opt = it.OptionalType(it.INT)
        assert opt.is_assignable_from(it.INT)
        assert opt.is_assignable_from(it.NULL)
        assert not opt.is_assignable_from(it.STRING)

    def test_list_type(self):
        lt = it.ListType(it.STRING)
        assert lt.is_assignable_from(it.ListType(it.STRING))
        assert str(lt) == "list[string]"

    def test_union_type(self):
        u = it.UnionType((it.STRING, it.INT))
        assert u.is_assignable_from(it.STRING)
        assert u.is_assignable_from(it.INT)
        assert str(u) == "string | int"

    def test_any_unknown_error_compatible_with_all(self):
        for special in (it.ANY, it.UNKNOWN, it.ERROR):
            for t in (it.STRING, it.INT, it.FLOAT, it.NULL):
                assert special.is_compatible(t)

    def test_map_type(self):
        m = it.MapType(it.STRING, it.INT)
        assert m.is_assignable_from(it.MapType(it.STRING, it.INT))
        assert str(m) == "map[string, int]"

    def test_duration_and_resource_types(self):
        assert str(it.DURATION) == "duration"
        assert str(it.RESOURCE) == "resource"
        assert str(it.PERCENTAGE) == "percentage"

    def test_infer_literal_type(self):
        assert it.infer_literal_type("x") == it.STRING
        assert it.infer_literal_type(42) == it.INT
        assert it.infer_literal_type(3.14) == it.FLOAT
        assert it.infer_literal_type(True) == it.BOOL
        assert it.infer_literal_type(None) == it.NULL
        assert it.infer_literal_type([1]) == it.ANY

    def test_are_types_compatible(self):
        assert it.are_types_compatible(it.STRING, it.STRING)
        assert not it.are_types_compatible(it.STRING, it.INT)

    def test_unify_types(self):
        assert it.unify_types([it.STRING, it.STRING]) == it.STRING
        assert it.unify_types([it.INT, it.FLOAT]) == it.FLOAT
        assert isinstance(it.unify_types([it.STRING, it.INT]), it.UnionType)
        assert it.unify_types([]) == it.ANY
        assert it.unify_types([it.ANY, it.STRING]) == it.ANY


class TestBaseBackend:
    def _ctx(self):
        return CompileContext(program=n.Program(), symbol_table=None)

    def test_evaluate_literals(self):
        ctx = self._ctx()
        assert evaluate_expression(n.Literal("hello"), ctx) == "hello"
        assert evaluate_expression(n.Literal(42), ctx) == 42
        assert evaluate_expression(n.Literal(True), ctx) is True
        assert evaluate_expression(n.Literal(None), ctx) is None
        assert evaluate_expression(n.Duration(30, "s"), ctx) == "30s"
        assert evaluate_expression(n.ResourceValue(128, "Mi"), ctx) == "128Mi"
        assert evaluate_expression(n.Percentage(25), ctx) == "25%"

    def test_evaluate_identifier_from_context(self):
        ctx = self._ctx()
        ctx.variables["X"] = n.Literal("hello")
        assert evaluate_expression(n.Identifier("X"), ctx) == "hello"

    def test_evaluate_identifier_missing(self):
        ctx = self._ctx()
        assert evaluate_expression(n.Identifier("MISSING"), ctx) == "${MISSING}"

    def test_evaluate_template_string(self):
        ctx = self._ctx()
        ctx.variables["TAG"] = n.Literal("v1")
        ts = n.TemplateString(parts=("nginx:", ("expr", "TAG")))
        assert evaluate_expression(ts, ctx) == "nginx:v1"

    def test_evaluate_binary_op(self):
        ctx = self._ctx()
        assert (
            evaluate_expression(n.BinaryOp(n.Literal(1), "+", n.Literal(2)), ctx) == 3
        )
        assert (
            evaluate_expression(n.BinaryOp(n.Literal("a"), "+", n.Literal("b")), ctx)
            == "ab"
        )

    def test_evaluate_list_and_map(self):
        ctx = self._ctx()
        assert evaluate_expression(
            n.List(items=(n.Literal("a"), n.Literal("b"))), ctx
        ) == ["a", "b"]

    def test_evaluate_call_builtin(self):
        ctx = self._ctx()
        call = n.Call(callee=n.Identifier("upper"), args=(n.Literal("hi"),))
        assert evaluate_expression(call, ctx) == "HI"

    def test_from_program_loads_variables(self):
        program = n.Program(
            statements=(n.VariableDecl(name="V", value=n.Literal("x")),)
        )
        ctx = CompileContext.from_program(program, symbol_table=None)
        assert "V" in ctx.variables

    def test_compile_result_empty(self):
        assert CompileResult(files={}).is_empty
        assert not CompileResult(files={"a": "b"}).is_empty
