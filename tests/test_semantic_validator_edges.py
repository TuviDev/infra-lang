"""Contract tests for previously untested SemanticValidator branches (v0.4.4).

Companion to the k8s_validator error-path tests: each test names the branch
it pins (see the coverage audit, `analyzer/validator.py`).
"""

from __future__ import annotations

from infra.analyzer import validator as vmod
from infra.analyzer.validator import SemanticValidator, _expr_type
from infra.parser import ast_nodes as n
from infra.parser import parse


def _validate(source: str, **kwargs):
    prog = parse(source)
    return SemanticValidator().validate(prog, **kwargs)


def _last(prog, typ):
    return [s for s in prog.statements if isinstance(s, typ)][-1]


class TestExprTypeFallbacks:
    """Lines 44/52/56: None / Percentage / Map expression type inference."""

    def test_none_expression_is_unknown(self):
        assert _expr_type(None) == vmod.T.UNKNOWN

    def test_percentage_expression(self):
        assert _expr_type(n.Percentage(value=80.0)) == vmod.T.PERCENTAGE

    def test_map_expression(self):
        m = n.Map(
            entries=(n.MapEntry(key=n.Literal(value="k"), value=n.Literal(value=1)),)
        )
        from infra.analyzer import types as t

        assert isinstance(_expr_type(m), t.MapType)

    def test_literal_expression(self):
        assert _expr_type(n.Literal(value=1)) != vmod.T.UNKNOWN


class TestValidateToggles:
    """Arcs 102->114 / 114->122: reliability/security checkers disabled."""

    def test_reliability_and_security_disabled(self):
        result = _validate(
            'service s { image: "x" }', reliability=False, security=False
        )
        assert result is not None

    def test_reliability_only(self):
        result = _validate('service s { image: "x" }', security=False)
        assert result is not None

    def test_security_only(self):
        result = _validate('service s { image: "x" }', reliability=False)
        assert result is not None


class TestVisitorFallbacks:
    """Lines 176, 186, 188->exit, 199-202: visitor and suggestion guards."""

    def test_suggest_exact_match_returns_none(self):
        v = SemanticValidator()
        assert v._suggest("api", {"api", "api2"}) is None

    def test_visit_none_is_noop(self):
        SemanticValidator()._visit(None)

    def test_visit_unhandled_node_type_is_noop(self):
        v = SemanticValidator()
        v._visit(n.Import(path="./x.infra", names=()))  # no dedicated handler
        assert v.result.errors == []

    def test_empty_variable_name_errors(self):
        v = SemanticValidator()
        v._visit_VariableDecl(
            n.VariableDecl(name="", value=n.Literal(value=1), const=False)
        )
        assert any("E040" in (e.code or "") for e in v.result.errors)


class TestExpressionRecursion:
    """Lines 242-246, 250-253, 258-261: Index/Attribute/Map/MatchExpr arms."""

    def test_index_expression_validated(self):
        result = _validate("let xs = [1, 2]\nlet y = xs[0]")
        assert result is not None

    def test_attribute_expression_validated(self):
        result = _validate("let y = foo.bar")
        assert result is not None

    def test_map_expression_validated(self):
        result = _validate('let m = { "a": 1 }')
        assert result is not None

    def test_match_expression_validated(self):
        result = _validate('let s = 1\nlet m = match s { 1 -> 2 _ -> 3 }')
        assert result is not None

    def test_if_expression_validated(self):
        result = _validate("let m = if ok then 1 else 2")
        assert result is not None


class TestServiceFieldGuards:
    """Lines 282, 325: build context / network-policy-absent guards."""

    def test_service_with_build_context(self):
        result = _validate(
            'service s { image: "x" build { context: "./app" } }'
        )
        assert result is not None

    def test_service_without_network_policy(self):
        result = _validate('service s { image: "x" }')
        assert result is not None

    def test_service_with_zero_replicas_flags(self):
        result = _validate('service s { image: "x" replicas: 0 }')
        assert result is not None


class TestResidualGuards:
    """Follow-up arcs: empty-candidate suggest, NetworkDef visit, provider hint."""

    def test_suggest_identifier_without_candidates(self):
        v = SemanticValidator()
        assert v._suggest_identifier("anything") is None

    def test_network_definition_registers(self):
        result = _validate('network dmz { cidr: "10.0.0.0/16" }')
        assert result is not None

    def test_environment_unknown_provider_warns(self):
        result = _validate('environment prod { provider: "awz" }')
        assert any("provider" in (e.message.lower()) for e in result.errors)


class TestDriftResidualBranches:
    """Residual `analyzer/drift.py` arcs: clean render, NDJSON blank/bad rows."""

    def test_render_drift_clean_message(self):
        from infra.analyzer.drift import DriftResult, render_drift

        out = render_drift(DriftResult(has_drift=False))
        assert out.startswith("No drift detected")

    def test_parse_compose_ps_skips_blank_and_non_dict_rows(self):
        from infra.analyzer.drift import _parse_compose_ps

        rows = _parse_compose_ps('{"Service": "a"}\n\n[1]\n{"Service": "b"}')
        assert [r["Service"] for r in rows] == ["a", "b"]
