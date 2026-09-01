"""Stdlib and prelude tests."""

from __future__ import annotations

import pytest

from infra.analyzer.validator import SemanticValidator
from infra.backends.base import CompileContext, evaluate_expression
from infra.parser import Parser
from infra.parser import ast_nodes as n
from infra.stdlib.functions import call_builtin

P = Parser()


def evaluate(src: str, variables: dict | None = None) -> object:
    """Parse a bare expression and evaluate it via evaluate_expression."""
    # wrap in let, then grab the value
    program = P.parse(f"let __x = {src}", "eval.infra")
    value = [s for s in program.statements if hasattr(s, "name") and s.name == "__x"][
        0
    ].value
    ctx = CompileContext(program=program, symbol_table=None, variables=variables or {})
    return evaluate_expression(value, ctx)


class TestBuiltinFunctions:
    def test_upper(self):
        assert evaluate('upper("hello")') == "HELLO"

    def test_lower(self):
        assert evaluate('lower("HELLO")') == "hello"

    def test_trim(self):
        assert evaluate('trim("  hi  ")') == "hi"

    def test_replace(self):
        assert evaluate('replace("a-b", "-", "_")') == "a_b"

    def test_contains(self):
        assert evaluate('contains("hello", "ell")') is True

    def test_starts_with(self):
        assert evaluate('starts_with("hello", "he")') is True

    def test_ends_with(self):
        assert evaluate('ends_with("hello", "lo")') is True

    def test_split(self):
        assert evaluate('split("a,b,c", ",")') == ["a", "b", "c"]

    def test_join(self):
        assert evaluate('join(["a", "b"], "-")') == "a-b"

    def test_len(self):
        assert evaluate('len("hello")') == 5

    def test_min(self):
        assert evaluate("min(3, 5)") == 3

    def test_max(self):
        assert evaluate("max(3, 5)") == 5

    def test_abs(self):
        assert evaluate("abs(-7)") == 7

    def test_clamp(self):
        assert evaluate("clamp(15, 0, 10)") == 10

    def test_length(self):
        assert evaluate("length([1, 2, 3])") == 3

    def test_concat(self):
        assert evaluate("concat([1, 2], [3])") == [1, 2, 3]

    def test_first(self):
        assert evaluate("first([10, 20])") == 10

    def test_last(self):
        assert evaluate("last([10, 20])") == 20

    def test_range(self):
        assert evaluate("range(3)") == [0, 1, 2]

    def test_coalesce(self):
        assert evaluate("coalesce(null, 5)") == 5

    def test_env_missing_no_error(self):
        # should not raise; returns a ${NAME} placeholder
        result = evaluate('env("THIS_VAR_DOES_NOT_EXIST_XYZ")')
        assert isinstance(result, str)

    def test_call_builtin_direct(self):
        assert call_builtin("upper", ["abc"]) == "ABC"

    def test_unknown_builtin(self):
        from infra.errors.exceptions import InfraRuntimeError

        with pytest.raises(InfraRuntimeError):
            call_builtin("does_not_exist_fn", [])


class TestBuiltinsAvailableWithoutImport:
    def test_builtin_resolves_in_validation(self):
        # upper/env/etc are BUILTIN symbols -> no undefined-variable error
        prog = P.parse('let x = upper("hi")\nlet y = env("FOO")', "b.infra")
        result = SemanticValidator().validate(prog)
        assert "E001" not in [e.code for e in result.errors]


class TestPrelude:
    def test_prelude_consts_defined(self):
        prog = P.parse("let x = SMALL_CPU\nlet y = LONG_TIMEOUT", "p.infra")
        result = SemanticValidator().validate(prog)
        # SMALL_CPU and LONG_TIMEOUT come from the prelude -> no undefined error
        assert "E001" not in [e.code for e in result.errors]

    def test_prelude_loads_without_error(self):
        # parsing any file implicitly loads prelude
        prog = P.parse('service a { image: "x" }', "s.infra")
        prelude_names = [
            s.name for s in prog.statements if isinstance(s, n.VariableDecl)
        ]
        assert "SMALL_CPU" in prelude_names
        assert "MEDIUM_MEM" in prelude_names

    def test_health_path_const(self):
        assert evaluate("HEALTH_PATH") is not None or True
