"""Contract tests for transformer coercion helpers and fallback paths (v0.4.4).

These cover the *reachable* helper/coercion branches identified by the
coverage audit (`_pick`, `_is_str_tuple`, `_is_str_pair_tuple`,
`_parse_template`, `_lit`, `_lit_list`, `_loc`) plus DSL-level coercion
paths (`unit_value` / `resource_value`, expression passthroughs, top-level
aggregation). The proven-unreachable LALR alternative-reduction tails carry
`# pragma: no cover` in the source instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from lark import Token

from infra.parser import ast_nodes as n
from infra.parser import parse, parse_expression
from infra.parser.transformer import (
    _STR_COERCE,
    InfraTransformer,
    _is_str_pair_tuple,
    _is_str_tuple,
    _lit,
    _lit_list,
    _loc,
    _pick,
)

_parse_template = InfraTransformer._parse_template


def _last(prog, typ):
    """parse() prepends the stdlib prelude; pick the last stmt of a type."""
    return [s for s in prog.statements if isinstance(s, typ)][-1]


class TestLitHelpers:
    def test_lit_literal_identifier_token(self):
        assert _lit(n.Literal(value="x")) == "x"
        assert _lit(n.Identifier(name="y")) == "y"
        assert _lit(Token("STRING", '"z"')) == '"z"'
        assert _lit(None) is None

    def test_lit_fallback_stringifies_unknown_types(self):
        # Non-token, non-node values fall through to str() (line 104 tail).
        assert _lit(42) == "42"

    def test_lit_list_python_sequence(self):
        # A plain python list/tuple walks the isinstance(list, tuple) arm.
        assert _lit_list([n.Literal(value="a"), n.Identifier(name="b")]) == (
            "a",
            "b",
        )

    def test_lit_list_infra_list_node(self):
        items = n.List(items=(n.Literal(value="a"),))
        assert _lit_list(items) == ("a",)

    def test_lit_list_unknown_returns_empty(self):
        assert _lit_list(None) == ()
        assert _lit_list(3.5) == ()


class TestLocHelper:
    def test_loc_happy_path(self):
        meta = Token("X", "x")  # has no line/file attributes -> defaults
        loc = _loc(meta)
        assert loc is not None

    def test_loc_exception_returns_none(self):
        class EvilMeta:
            @property
            def line(self):
                raise RuntimeError("boom")

        assert _loc(EvilMeta()) is None


class TestIsStrTupleHelpers:
    def test_str_tuple_true(self):
        assert _is_str_tuple(Tuple[str, ...]) is True

    def test_str_tuple_false_variants(self):
        assert _is_str_tuple(Tuple[int, ...]) is False  # wrong element type
        assert _is_str_tuple(Tuple[str, str]) is False  # not variadic
        assert _is_str_tuple(str) is False  # not a tuple at all
        assert _is_str_tuple(Optional[str]) is False

    def test_str_pair_tuple_true(self):
        assert _is_str_pair_tuple(Tuple[Tuple[str, str], ...]) is True

    def test_str_pair_tuple_false_variants(self):
        assert _is_str_pair_tuple(Tuple[str, ...]) is False
        assert _is_str_pair_tuple(Tuple[Tuple[str, int], ...]) is False
        assert _is_str_pair_tuple(Tuple[Tuple[str, str, str], ...]) is False
        assert _is_str_pair_tuple(Tuple[Tuple[str, str], str]) is False
        assert _is_str_pair_tuple(dict) is False


class TestParseTemplate:
    def test_plain_text_only(self):
        assert _parse_template("no braces here") == ["no braces here"]

    def test_expression_and_text_interleave(self):
        assert _parse_template("a {x} b {y} c") == [
            "a ",
            ("expr", "x"),
            " b ",
            ("expr", "y"),
            " c",
        ]

    def test_nested_braces_are_balanced(self):
        parts = _parse_template("pre {outer {inner} rest} post")
        assert parts == ["pre ", ("expr", "outer {inner} rest"), " post"]

    def test_adjacent_expressions(self):
        assert _parse_template("{a}{b}") == [("expr", "a"), ("expr", "b")]

    def test_unterminated_brace_keeps_scanned_expr(self):
        # Documented tolerant behaviour: a dangling '{' ends the scan and
        # the characters read so far become the expression part.
        assert _parse_template("x {abc") == ["x ", ("expr", "abc")]

    def test_template_string_token_end_to_end(self):
        # DSL level: the template string rule routes through _parse_template.
        prog = parse("let t = `hello { name }!`")
        value = _last(prog, n.VariableDecl).value
        assert isinstance(value, n.TemplateString)
        assert value.parts == ("hello ", ("expr", " name "), "!")


class TestPickCoercions:
    def test_scalar_literal_coercions(self):
        @dataclass
        class S:
            name: str
            count: Optional[int] = None
            ratio: Optional[float] = None
            title: Optional[str] = None
            flag: Optional[bool] = None

        out = _pick(
            {
                "name": "ignored-by-design",
                "count": n.Literal(value="3"),
                "ratio": n.Literal(value="1.5"),
                "title": n.Literal(value="hello"),
                "flag": n.Literal(value="true"),
            },
            S,
        )
        assert out == {"count": 3, "ratio": 1.5, "title": "hello", "flag": True}

    def test_identifier_bool_and_str_coerce(self):
        @dataclass
        class S:
            name: str
            flag: Optional[bool] = None
            type: Optional[str] = None  # in _STR_COERCE

        assert "type" in _STR_COERCE
        out = _pick(
            {"flag": n.Identifier(name="true"), "type": n.Identifier(name="spot")},
            S,
        )
        assert out == {"flag": True, "type": "spot"}

    def test_duration_to_float_and_resource_value(self):
        @dataclass
        class S:
            name: str
            timeout: Optional[float] = None
            rv: Optional[n.ResourceValue] = None

        out = _pick(
            {
                "timeout": n.Duration(value=30.0, unit="s"),
                "rv": n.Duration(value=500.0, unit="Mi"),
            },
            S,
        )
        assert out["timeout"] == 30.0
        rv = out["rv"]
        assert isinstance(rv, n.ResourceValue)
        assert (rv.value, rv.unit) == (500.0, "Mi")

    def test_list_and_map_tuple_coercions(self):
        @dataclass
        class S:
            name: str
            deps: Tuple[str, ...] = ()
            labels: Tuple[Tuple[str, str], ...] = ()

        out = _pick(
            {
                "deps": n.List(items=(n.Literal(value="a"), n.Identifier(name="b"))),
                "labels": n.Map(
                    entries=(
                        n.MapEntry(
                            key=n.Literal(value="k"),
                            value=n.Literal(value="v"),
                        ),
                    )
                ),
            },
            S,
        )
        assert out == {"deps": ("a", "b"), "labels": (("k", "v"),)}

    def test_passthrough_exclude_and_skip(self):
        @dataclass
        class S:
            name: str
            keep: Optional[dict] = None
            excluded: Optional[str] = None
            absent: Optional[str] = None

        marker = object()
        out = _pick({"keep": marker, "excluded": "x"}, S, exclude=("excluded",))
        assert out == {"keep": marker}  # non-coercible values pass through
        assert "name" not in out
        assert "absent" not in out


class TestUnitValueAndResourceCoercions:
    """DSL-level: `unit_value: UNIT_NUMBER` is the only referenced rule; the
    `duration` rule is unused by the grammar (pragma'd), but `resource_value`
    coercions are reachable and verified here."""

    def test_resource_quantity_string_unit(self):
        svc = _last(
            parse('service s { image: "x" resources { cpu: 500m } }'), n.ServiceDef
        )
        cpu = svc.resources.requests.cpu
        assert isinstance(cpu, n.ResourceValue)
        assert (cpu.value, cpu.unit) == (500.0, "m")

    def test_resource_value_plain_number(self):
        svc = _last(
            parse('service s { image: "x" resources { cpu: 2 } }'), n.ServiceDef
        )
        cpu = svc.resources.requests.cpu
        assert isinstance(cpu, n.ResourceValue)
        assert cpu.value == 2.0

    def test_resource_value_identifier_reference(self):
        svc = _last(
            parse('service s { image: "x" resources { cpu: APP_CPU } }'),
            n.ServiceDef,
        )
        cpu = svc.resources.requests.cpu
        assert isinstance(cpu, n.Identifier)
        assert cpu.name == "APP_CPU"

    def test_resource_value_time_quantity_coerced(self):
        # A time-unit quantity in a resource slot is coerced Duration ->
        # ResourceValue (documented coercion branch).
        svc = _last(
            parse('service s { image: "x" resources { memory: 500ms } }'),
            n.ServiceDef,
        )
        mem = svc.resources.requests.memory
        assert isinstance(mem, n.ResourceValue)

    def test_scale_up_delay_uses_unit_value(self):
        prog = parse(
            'service api { image: "x" autoscale { min: 1, max: 5, '
            "scale_up_delay: 30s } }"
        )
        svc = _last(prog, n.ServiceDef)
        assert svc.autoscale is not None


class TestExpressionPassthroughs:
    def test_bare_identifier(self):
        expr = parse_expression("x")
        assert isinstance(expr, n.Identifier)

    def test_if_expression(self):
        expr = parse_expression("if a then b else c")
        assert isinstance(expr, n.IfExpr)

    def test_not_expression(self):
        expr = parse_expression("!flag")
        assert isinstance(expr, n.UnaryOp)
        assert expr.operator == "!"

    def test_negative_expression(self):
        expr = parse_expression("-x")
        assert isinstance(expr, n.UnaryOp)
        assert expr.operator == "-"

    def test_parenthesized_and_binary(self):
        expr = parse_expression("(1 + 2) * 3")
        assert isinstance(expr, n.BinaryOp)
        assert expr.operator == "*"


class TestTopLevelAggregation:
    def test_environment_def_statements_aggregated(self):
        # `environment dev { ... }` definitions are regular statements; the
        # string-named overlay form feeds Program.environments via start().
        prog = parse('environment dev { description: "d" }')
        assert isinstance(_last(prog, n.EnvironmentDef), n.EnvironmentDef)
        overlay = parse('environment "prod" { service web { replicas: 2 } }')
        assert len(overlay.environments) == 1
        assert isinstance(overlay.environments[0], n.EnvironmentSpec)

    def test_import_top_level_collected(self):
        prog = parse('import "./lib.infra"')
        assert prog.imports
        assert prog.imports[0].path == "./lib.infra"

    def test_selective_import_names(self):
        prog = parse('from "./lib.infra" import alpha, beta')
        assert prog.imports[0].names == ("alpha", "beta")

    def test_float_literal(self):
        value = _last(parse("const PI = 3.14"), n.VariableDecl).value
        assert isinstance(value, n.Literal)
        assert value.value == 3.14

    def test_match_arm_string_and_wildcard(self):
        prog = parse('let m = match s { "x" -> 1 true -> 2 _ -> 0 }')
        value = _last(prog, n.VariableDecl).value
        assert isinstance(value, n.MatchExpr)
        assert len(value.arms) == 3

    def test_service_body_port_and_volume_specs(self):
        svc = _last(
            parse(
                'service s { image: "x" port 8080 '
                'volumes [{ name: "data" mount_path: "/data" readonly: true }] }'
            ),
            n.ServiceDef,
        )
        assert svc.ports
        assert svc.volumes

    def test_str_none_coerces_to_empty(self):
        from infra.parser.transformer import _str

        assert _str(None) == ""


class TestDispatchChainVariantSweeps:
    """Geometry variants that sweep the *reachable* elif-chain arcs in the
    resource/probe/affinity/schedule dispatch handlers (audit follow-up)."""

    SVC = 'service s { image: "x" %s }'

    def _svc(self, body: str):
        prog = parse(self.SVC % body)
        return _last(prog, n.ServiceDef)

    def test_resources_limits_only(self):
        svc = self._svc("resources { limits { cpu: 2 } }")
        assert svc.resources.limits is not None

    def test_resources_requests_and_limits(self):
        svc = self._svc(
            "resources { requests { cpu: 100m, memory: 128Mi }, "
            "limits { memory: 512Mi } }"
        )
        assert svc.resources.requests is not None
        assert svc.resources.limits is not None

    def test_probes_single_entry(self):
        svc = self._svc('probes { liveness http("/h") { interval: 5s } }')
        assert svc.probes is not None

    def test_probes_all_three(self):
        svc = self._svc(
            'probes { liveness http("/h"), readiness http("/r"), startup http("/s") }'
        )
        assert svc.probes is not None

    def test_network_policy_egress_only(self):
        svc = self._svc('network_policy { allow_egress: ["db"] }')
        assert svc.network_policy is not None

    def test_network_policy_deny_only(self):
        svc = self._svc('network_policy { deny_from: ["*"] }')
        assert svc.network_policy is not None

    def test_schedule_single_key(self):
        svc = self._svc('schedule { "0 9 * *": replicas 1 }')
        assert svc.schedule is not None

    def test_topology_spread_by(self):
        svc = self._svc("topology { spread_by: zone }")
        assert svc.topology is not None

    def test_affinity_prefer_same(self):
        svc = self._svc("affinity { prefer_same: [frontend] }")
        assert svc.affinity is not None

    def test_autoscale_min_only(self):
        svc = self._svc("autoscale { min: 2 }")
        assert svc.autoscale is not None

    def test_disruption_min_available(self):
        svc = self._svc("disruption { min_available: 1 }")
        assert svc.disruption is not None
