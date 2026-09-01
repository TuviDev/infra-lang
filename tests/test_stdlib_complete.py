"""Completeness: every stdlib builtin and unit conversion is exercised."""

from __future__ import annotations

import pytest

from infra.backends.base import CompileContext, evaluate_expression
from infra.parser import ast_nodes as n
from infra.stdlib.functions import STDLIB, call_builtin


def eval_expr(src: str, variables: dict | None = None):
    from infra import parse

    program = parse(f"let __x = {src}")
    value = [s for s in program.statements if hasattr(s, "name") and s.name == "__x"][
        0
    ].value
    ctx = CompileContext(program=program, symbol_table=None, variables=variables or {})
    return evaluate_expression(value, ctx)


class TestRegistry:
    def test_all_names(self):
        names = STDLIB.all_names()
        assert "env" in names and "upper" in names and "length" in names

    def test_is_builtin(self):
        assert STDLIB.is_builtin("upper")
        assert not STDLIB.is_builtin("nope")

    def test_get(self):
        assert STDLIB.get("upper") is not None
        assert STDLIB.get("nope") is None


class TestStringFunctions:
    @pytest.mark.parametrize(
        "fn,arg,expected",
        [
            ("upper('hi')", None, "HI"),
            ("lower('HI')", None, "hi"),
            ("trim('  x  ')", None, "x"),
            ("replace('a-b', '-', '_')", None, "a_b"),
            ("contains('hello', 'ell')", None, True),
            ("starts_with('hello', 'he')", None, True),
            ("ends_with('hello', 'lo')", None, True),
            ("split('a,b', ',')", None, ["a", "b"]),
            ("join(['a','b'], '-')", None, "a-b"),
            ("len('abcd')", None, 4),
        ],
    )
    def test_functions(self, fn, arg, expected):
        assert eval_expr(fn) == expected


class TestMathFunctions:
    @pytest.mark.parametrize(
        "fn,expected",
        [
            ("min(3, 5)", 3),
            ("max(3, 5)", 5),
            ("abs(-7)", 7),
            ("clamp(15, 0, 10)", 10),
            ("clamp(-5, 0, 10)", 0),
        ],
    )
    def test_math(self, fn, expected):
        assert eval_expr(fn) == expected


class TestListFunctions:
    @pytest.mark.parametrize(
        "fn,expected",
        [
            ("length([1,2,3])", 3),
            ("concat([1],[2])", [1, 2]),
            ("first([10,20])", 10),
            ("last([10,20])", 20),
            ("range(3)", [0, 1, 2]),
        ],
    )
    def test_list(self, fn, expected):
        assert eval_expr(fn) == expected

    def test_first_empty(self):
        assert eval_expr("first([])") is None

    def test_last_empty(self):
        assert eval_expr("last([])") is None


class TestEnvFunctions:
    def test_env_with_default(self, monkeypatch):
        monkeypatch.setenv("INFRA_TEST_VAR", "hello")
        assert eval_expr('env("INFRA_TEST_VAR")') == "hello"

    def test_env_placeholder(self, monkeypatch):
        monkeypatch.delenv("INFRA_MISSING_VAR", raising=False)
        result = eval_expr('env("INFRA_MISSING_VAR")')
        assert "${INFRA_MISSING_VAR}" == result

    def test_coalesce(self):
        assert eval_expr("coalesce(null, 5)") == 5


class TestCallBuiltin:
    def test_direct(self):
        assert call_builtin("upper", ["abc"]) == "ABC"

    def test_unknown_raises(self):
        from infra.errors.exceptions import InfraRuntimeError

        with pytest.raises(InfraRuntimeError):
            call_builtin("does_not_exist", [])

    def test_bad_args_raises(self):
        from infra.errors.exceptions import InfraRuntimeError

        with pytest.raises(InfraRuntimeError):
            call_builtin("upper", [1, 2])


class TestDurationConversion:
    def test_to_seconds_all(self):
        for unit, secs in [
            ("ms", 0.001),
            ("s", 1),
            ("min", 60),
            ("h", 3600),
            ("d", 86400),
            ("w", 604800),
        ]:
            assert n.Duration(1, unit).to_seconds() == secs

    def test_unknown_unit_defaults_1(self):
        assert n.Duration(1, "xx").to_seconds() == 1.0


class TestResourceConversion:
    def test_to_kubernetes_memory(self):
        for unit in ["Ki", "Mi", "Gi", "Ti"]:
            assert n.ResourceValue(2, unit).to_kubernetes() == f"2{unit}"

    def test_to_bytes(self):
        assert n.ResourceValue(1, "Ki").to_bytes() == 1024
        assert n.ResourceValue(1, "Ti").to_bytes() == 1024**4
        assert n.ResourceValue(500, "m").to_bytes() == 500
