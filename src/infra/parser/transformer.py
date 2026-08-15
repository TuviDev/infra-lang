# ruff: noqa: E501
"""Lark -> AST transformer.

Each method corresponds to a grammar rule from ``grammar.lark`` and builds the
corresponding node from :mod:`infra.parser.ast_nodes``. ``@v_args(meta=True)``
is used so rule methods receive ``(meta, children)``.
"""
# mypy: disable-error-code="no-untyped-def,no-untyped-call,no-any-return,type-arg,var-annotated"
# ruff: noqa: N802, N812

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional, Tuple

from lark import Token, Transformer, v_args

from infra.parser import ast_nodes as n
from infra.parser.ast_nodes import (  # noqa: F401  (re-export convenience)
    SourceLocation,
)

# Map a Lark terminal *name* to the canonical AST field name, used when the
# token value differs from the desired attribute (mostly camelCase).
_FIELD = {
    "START_PERIOD": "start_period",
    "INITIAL_DELAY": "initial_delay",
    "MOUNT_PATH": "mount_path",
    "HOST_PATH": "host_path",
    "READONLY": "read_only",
    "ACCESS_MODE": "access_mode",
    "STORAGE_CLASS": "storage_class",
    "RESTORE_KEYS": "restore_keys",
    "CANCEL_IN_PROGRESS": "cancel_in_progress",
    "READONLY_ROOT": "read_only_root_filesystem",
    "CONTINUE_ON_ERROR": "continue_on_error",
    "TARGET_CPU": "target_cpu",
    "TARGET_MEMORY": "target_memory",
    "SCALE_UP_DELAY": "scale_up_delay",
    "SCALE_DOWN_DELAY": "scale_down_delay",
    "MIN_AVAILABLE": "min_available",
    "MAX_UNAVAILABLE": "max_unavailable",
    "ALLOW_FROM": "allow_from",
    "DENY_FROM": "deny_from",
    "ALLOW_EGRESS": "allow_egress",
    "SPREAD_BY": "spread_by",
    "MAX_SKEW": "max_skew",
    "MAX_CPU": "max_cpu",
    "MAX_MEMORY": "max_memory",
            "MAX_PODS": "max_pods",
    "RUNS_ON": "runs_on",
}


def _loc(meta: Any) -> Optional[n.SourceLocation]:
    try:
        return n.SourceLocation(
            file=getattr(meta, "file", None) or getattr(_CUR_FILE, "value", "<string>"),
            line=getattr(meta, "line", 1) or 1,
            column=getattr(meta, "column", 1) or 1,
            end_line=getattr(meta, "end_line", 0) or 0,
            end_column=getattr(meta, "end_column", 0) or 0,
        )
    except Exception:
        return None


class _CurFile:
    value: str = "<string>"


_CUR_FILE = _CurFile()


def _set_file(name: str) -> None:
    _CUR_FILE.value = name


def _str(t: Any) -> str:
    if isinstance(t, Token):
        return str(t.value)
    if isinstance(t, n.Literal):
        return str(t.value)
    if t is None:
        return ""
    return str(t)


def _lit(v: Any) -> Optional[str]:
    if isinstance(v, n.Literal):
        return str(v.value)
    if isinstance(v, n.Identifier):
        return v.name
    if isinstance(v, Token):
        return str(v.value)
    if v is None:
        return None
    return str(v)


def _lit_list(v) -> Tuple[str, ...]:
    if isinstance(v, n.List):
        return tuple(_lit(x) for x in v.items)  # type: ignore[misc]
    if isinstance(v, (list, tuple)):
        return tuple(_lit(x) for x in v)  # type: ignore[misc]
    if v is None:
        return ()
    return ()


def _num(v: Any) -> float:
    if isinstance(v, n.Literal):
        return float(v.value)
    return float(v)


def _int(v: Any) -> int:
    return int(_num(v))


def _bool(v: Any) -> bool:
    """Coerce a parsed value to bool.

    A bare `true`/`false` arrives as an n.Literal(value=bool); naively calling
    ``bool(...)`` on that node is always True, so we must unwrap the value.
    """
    if isinstance(v, n.Literal):
        return bool(v.value)
    if isinstance(v, n.Identifier):
        return v.name == "true"
    return bool(v)


def _int_list(v) -> Tuple[int, ...]:
    if isinstance(v, n.List):
        return tuple(_int(x) for x in v.items)
    if isinstance(v, (list, tuple)):
        return tuple(_int(x) for x in v)
    return ()


def _name(t: Token) -> str:
    """Canonical field name from a field-name token."""
    return _FIELD.get(t.type, str(t.value).lower())


@v_args(meta=True)
class InfraTransformer(Transformer):
    """Transform Lark's parse tree into Infra AST nodes."""

    # ------------------------------------------------------------------ #
    # Top level
    # ------------------------------------------------------------------ #

    def start(self, meta, children):
        statements = []
        imports = []
        for c in children:
            if isinstance(c, n.Program):
                statements.extend(c.statements)
                imports.extend(c.imports)
            elif isinstance(c, n.Import):
                imports.append(c)
            elif c is not None:
                statements.append(c)
        return n.Program(statements=tuple(statements), imports=tuple(imports))

    def item(self, meta, children):
        defs = [c for c in children if not isinstance(c, n.Decorator)]
        decs = [c for c in children if isinstance(c, n.Decorator)]
        if not defs:
            return None
        target = defs[0]
        if decs and isinstance(
            target,
            (
                n.ServiceDef,
                n.DatabaseDef,
                n.CacheDef,
                n.QueueDef,
                n.StorageDef,
                n.NetworkDef,
                n.SecretDef,
                n.ConfigDef,
                n.PipelineDef,
                n.EnvironmentDef,
                n.ClusterDef,
            ),
        ):
            target = replace(target, decorators=tuple(decs))
        return target

    def decorator(self, meta, children):
        name = ""
        args: Tuple[Any, ...] = ()
        kwargs: Tuple[Tuple[str, Any], ...] = ()
        for c in children:
            if isinstance(c, Token) and c.type == "IDENTIFIER":
                name = c.value
            elif isinstance(c, tuple) and c and isinstance(c[0], tuple):
                args, kwargs = c
        return n.Decorator(name=name, args=tuple(args), kwargs=tuple(kwargs))

    def call_args(self, meta, children):
        args = []
        kwargs = []
        for c in children:
            if isinstance(c, Token) and c.type == "COMMA":
                continue
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                kwargs.append(c)
            else:
                args.append(c)
        return (tuple(args), tuple(kwargs))

    def call_arg(self, meta, children):
        if len(children) == 1:
            return children[0]
        return (children[0].name, children[2])

    def import_stmt(self, meta, children):
        if children[0].type == "IMPORT":
            path = _lit(children[1])
            alias = None
            for i, c in enumerate(children):
                if getattr(c, "type", "") == "AS":
                    alias = _str(children[i + 1])
            return n.Import(path=path or "", alias=alias)
        # from STRING import names
        path = _lit(children[1]) or ""
        names = []
        for c in children:
            if isinstance(c, list):
                names.extend(_str(x) for x in c)
            elif isinstance(c, Token) and c.type == "IDENTIFIER":
                names.append(c.value)
        return n.Import(path=path, names=tuple(names))

    def import_names(self, meta, children):
        return [_str(c) for c in children if not (isinstance(c, Token) and c.type == "COMMA")]

    def decl_kind(self, meta, children):
        return _str(children[0])

    def from_source(self, meta, children):
        return _str(children[0])

    def secret_source(self, meta, children):
        return _str(children[0])

    def variable_decl(self, meta, children):
        kind = _str(children[0])
        return n.VariableDecl(
            name=_str(children[1]),
            value=children[3],
            const=(kind == "const"),
            location=_loc(meta),
        )

    # ------------------------------------------------------------------ #
    # Literals
    # ------------------------------------------------------------------ #

    def INTEGER(self, token):
        v = token.value
        if v.lower().startswith("0x"):
            return n.Literal(value=int(v, 16))
        if v.lower().startswith("0b"):
            return n.Literal(value=int(v, 2))
        if "." in v or "e" in v.lower():
            return n.Literal(value=float(v))
        return n.Literal(value=int(v))

    def FLOAT(self, token):
        return n.Literal(value=float(token.value))

    def NUMBER(self, token):
        return self.INTEGER(token)

    def STRING(self, token):
        return n.Literal(value=token.value[1:-1])

    def TRUE(self, token):
        return n.Literal(value=True)

    def FALSE(self, token):
        return n.Literal(value=False)

    def NULL(self, token):
        return n.Literal(value=None)

    # "m" is milli (resource/CPU) — minutes must be written "min".
    _RESOURCE_UNITS = {
        "Ki",
        "Mi",
        "Gi",
        "Ti",
        "KiB",
        "MiB",
        "GiB",
        "MB",
        "GB",
        "TB",
        "m",
        "cores",
        "n",
    }
    _TIME_UNITS = {"ms", "s", "min", "h", "d", "w"}

    def unit_value(self, meta, children):
        import re as _re

        m = _re.match(r"(\d+(?:\.\d+)?)([a-zA-Z]+)", _str(children[0]))
        value = float(m.group(1)) if m else 0.0
        unit = m.group(2) if m else ""
        if unit in self._RESOURCE_UNITS:
            return n.ResourceValue(value=value, unit=unit)
        return n.Duration(value=value, unit=unit)

    def duration(self, meta, children):
        if isinstance(children[0], n.Duration):
            return children[0]
        if isinstance(children[0], n.ResourceValue):
            return n.Duration(value=children[0].value, unit=children[0].unit)
        return children[0]

    def resource_value(self, meta, children):
        c = children[0]
        if isinstance(c, n.ResourceValue):
            return n.ResourceValue(value=c.value, unit=c.unit)
        if isinstance(c, n.Duration):
            return n.ResourceValue(value=c.value, unit=c.unit)
        if isinstance(c, n.Identifier):
            # expression reference (e.g. cpu: APP_CPU) — resolved at compile time
            return c
        if isinstance(c, Token) and c.type == "IDENTIFIER":
            return n.Identifier(name=c.value, location=_loc(meta))
        if isinstance(c, n.Literal):
            return n.ResourceValue(value=float(c.value), unit="")
        return n.ResourceValue(value=float(_num(c)), unit="")

    def percentage(self, meta, children):
        return n.Percentage(value=float(_num(children[0])))

    def template_string(self, meta, children):
        parts = self._parse_template(_str(children[0])[1:-1])
        return n.TemplateString(parts=tuple(parts))

    # ------------------------------------------------------------------ #
    # Expressions
    # ------------------------------------------------------------------ #

    def expression(self, meta, children):
        return children[0]

    def if_expr(self, meta, children):
        if len(children) == 1:
            return children[0]
        return n.IfExpr(
            condition=children[1],
            then_branch=children[3],
            else_branch=children[5],
            location=_loc(meta),
        )

    def or_expr(self, meta, children):
        return self._binary(children)

    def and_expr(self, meta, children):
        return self._binary(children)

    def not_expr(self, meta, children):
        if len(children) == 1:
            return children[0]
        return n.UnaryOp(operator="!", operand=children[1])

    def comparison(self, meta, children):
        return self._binary(children)

    def sum_expr(self, meta, children):
        return self._binary(children)

    def term(self, meta, children):
        return self._binary(children)

    def factor(self, meta, children):
        if len(children) == 1:
            return children[0]
        return n.UnaryOp(operator="-", operand=children[1])

    def power(self, meta, children):
        return self._binary(children)

    def unary(self, meta, children):
        return children[0]

    def primary(self, meta, children):
        return children[0]

    def call_chain(self, meta, children):
        result = children[0]
        for tail in children[1:]:
            if isinstance(tail, tuple) and tail and tail[0] == "call":
                result = n.Call(callee=result, args=tail[1], kwargs=tail[2])
            elif isinstance(tail, tuple) and tail and tail[0] == "attr":
                result = n.Attribute(obj=result, attr=tail[1])
            elif isinstance(tail, tuple) and tail and tail[0] == "index":
                result = n.Index(obj=result, index=tail[1])
        return result

    def call_tail(self, meta, children):
        if children[0].type in ("LPAR", "LPAREN"):
            args, kwargs = children[1]
            return ("call", tuple(args), tuple(kwargs))
        if children[0].type in ("DOT",):
            return ("attr", _str(children[1]))
        return ("index", children[1])

    def atom(self, meta, children):
        if len(children) == 3:
            return children[1]
        return n.Identifier(name=_str(children[0]), location=_loc(meta))

    def pattern(self, meta, children):
        c = children[0]
        if isinstance(c, n.Literal):
            return c
        if isinstance(c, Token):
            if c.type in ("NUMBER", "STRING", "TRUE", "FALSE"):
                return n.Literal(value=_lit(c))
            if c.type == "UNDERSCORE":
                return None
            return n.Identifier(name=_str(c))
        return c

    def list_literal(self, meta, children):
        return n.List(items=tuple(c for c in children if not isinstance(c, Token)))

    def map_literal(self, meta, children):
        return n.Map(entries=tuple(c for c in children if isinstance(c, n.MapEntry)))

    def map_entry(self, meta, children):
        return n.MapEntry(key=children[0], value=children[2])

    def match_expr(self, meta, children):
        arms = tuple(c for c in children if isinstance(c, n.MatchArm))
        return n.MatchExpr(subject=children[1], arms=arms, location=_loc(meta))

    def match_arm(self, meta, children):
        return n.MatchArm(pattern=children[0], body=children[2])

    def UNDERSCORE(self, token):
        return None

    def definition(self, meta, children):
        return children[0]

    # ------------------------------------------------------------------ #
    # Generic helpers for body fields
    # ------------------------------------------------------------------ #

    def _field(self, children):
        """Build (field_name, value) from ``[NAME_TOKEN, COLON, value]``."""
        return (_name(children[0]), children[2])

    def _field_or(self, children, key, value):
        """Return a ``(key, value)`` tuple for a block field."""
        return (key, value)

    def _body_dict(self, children):
        """Collapse item children into a dict, mapping typed objects to keys."""
        out: dict = {}
        ports = []
        volumes = []
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                k, v = c
                if k == "port":
                    ports.append(v)
                elif k == "volume":
                    volumes.append(v)
                else:
                    out[k] = v
            elif isinstance(c, n.PortSpec):
                ports.append(c)
            elif isinstance(c, n.VolumeSpec):
                volumes.append(c)
            elif isinstance(c, dict):
                out.update(c)
        if ports:
            out["ports"] = tuple(ports)
        if volumes:
            out["volumes"] = tuple(volumes)
        return out

    # ------------------------------------------------------------------ #
    # Service
    # ------------------------------------------------------------------ #

    def service_def(self, meta, children):
        name = _str(children[1])
        extends = None
        if len(children) >= 5 and isinstance(children[2], Token) and children[2].type == "EXTENDS":
            extends = _str(children[3])
            body = children[5] if len(children) > 5 else {}
        else:
            body = children[3] if len(children) > 3 else {}
        return n.ServiceDef(name=name, extends=extends, **_pick(body, n.ServiceDef),
                            location=_loc(meta))

    def service_body(self, meta, children):
        return self._body_dict(children)

    def service_item(self, meta, children):
        for c in children:
            if isinstance(c, n.PortSpec):
                return ("port", c)
            if isinstance(c, n.BuildSpec):
                return ("build", c)
            if isinstance(c, n.ResourcesSpec):
                return ("resources", c)
            if isinstance(c, n.HealthSpec):
                return ("health", c)
            if isinstance(c, n.ProbesSpec):
                return ("probes", c)
            if isinstance(c, n.StrategySpec):
                return ("strategy", c)
            if isinstance(c, n.SecuritySpec):
                return ("security", c)
            if isinstance(c, n.LifecycleSpec):
                return ("lifecycle", c)
            if isinstance(c, n.IngressSpec):
                return ("ingress", c)
            if isinstance(c, n.ScheduleSpec):
                return ("schedule", c)
            if isinstance(c, n.AutoscaleSpec):
                return ("autoscale", c)
            if isinstance(c, n.DisruptionSpec):
                return ("disruption", c)
            if isinstance(c, n.NetworkPolicySpec):
                return ("network_policy", c)
            if isinstance(c, n.TopologySpec):
                return ("topology", c)
            if isinstance(c, n.AffinitySpec):
                return ("affinity", c)
            if isinstance(c, n.VolumeSpec):
                return ("volume", c)
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                if c[0] == "env":
                    return ("env", c[1])
                if c[0] == "env_from":
                    return ("env_from", c[1])
                return c
            if isinstance(c, tuple) and c and isinstance(c[0], n.EnvEntry):
                return ("env", c)
            if isinstance(c, tuple) and c and isinstance(c[0], n.EnvFromSpec):
                return ("env_from", c)
            if isinstance(c, tuple) and c and isinstance(c[0], n.VolumeSpec):
                return ("volumes", c)
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[-1] if children else None

    def scalar_field(self, meta, children):
        return (_str(children[0]), children[2])

    # Build
    def build_block(self, meta, children):
        fields = self._body_dict(children)
        return n.BuildSpec(
            context=_lit(fields.get("context")),
            dockerfile=_lit(fields.get("dockerfile")),
            target=_lit(fields.get("target")),
            args=self._pairs(fields.get("args")),
        )

    def build_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    # Ports
    def port_spec(self, meta, children):
        # [PORT, NUMBER] | [PORT, NUMBER, ":", NUMBER] | [PORT, port_object]
        if isinstance(children[-1], n.PortSpec):
            return children[-1]
        if len(children) == 2:
            return n.PortSpec(target=_int(children[1]))
        if len(children) == 4:
            # host : target
            return n.PortSpec(host=_int(children[1]), target=_int(children[3]))
        return children[-1]

    def port_value(self, meta, children):
        # [NUMBER] | [NUMBER, ":", NUMBER] | [port_object]
        if isinstance(children[0], n.PortSpec):
            return children[0]
        if len(children) == 1:
            return n.PortSpec(target=_int(children[0]))
        # host : target
        return n.PortSpec(host=_int(children[0]), target=_int(children[2]))

    def port_object(self, meta, children):
        fields = self._body_dict(children)
        return n.PortSpec(
            host=_int(fields["host"]) if "host" in fields else None,
            target=_int(fields["target"]) if "target" in fields else None,
            protocol=_lit(fields.get("protocol")),
        )

    def port_field(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    # Ingress
    def ingress_block(self, meta, children):
        fields = self._body_dict(children)
        return n.IngressSpec(
            host=_lit(fields.get("host")),
            domain=_lit(fields.get("domain")),
            tls=bool(fields.get("tls", False)),
            paths=_lit_list(fields.get("paths")),
            rate_limit=fields.get("rate_limit"),
            cors=fields.get("cors"),
        )

    def ingress_item(self, meta, children):
        for c in children:
            if isinstance(c, n.RateLimitSpec):
                return ("rate_limit", c)
            if isinstance(c, n.CorsSpec):
                return ("cors", c)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def rate_limit_spec(self, meta, children):
        fields = self._body_dict(children)
        return n.RateLimitSpec(
            rps=_num(fields["rps"]) if "rps" in fields else None,
            burst=_int(fields["burst"]) if "burst" in fields else None,
        )

    def rate_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def cors_block(self, meta, children):
        fields = self._body_dict(children)
        return n.CorsSpec(
            origins=_lit_list(fields.get("origins")),
            methods=_lit_list(fields.get("methods")),
            headers=_lit_list(fields.get("headers")),
            credentials=(
                bool(fields["credentials"]) if "credentials" in fields else None
            ),
        )

    def cors_item(self, meta, children):
        for c in children:
            if isinstance(c, n.CorsSpec):
                return ("cors", c)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    # Env
    def env_block(self, meta, children):
        return tuple(c for c in children if isinstance(c, n.EnvEntry))

    def env_entry(self, meta, children):
        name = _str(children[0])
        value = children[2]
        if isinstance(value, dict):
            return n.EnvEntry(name=name, **value)
        return n.EnvEntry(name=name, value=value)

    def env_value(self, meta, children):
        if len(children) == 1:
            return children[0]
        source = _str(children[1])
        name = _str(children[2])
        key = _str(children[4]) if len(children) >= 5 else None
        full = f"{name}.{key}" if key else name
        if source == "secret":
            return {"from_secret": full}
        if source == "config":
            return {"from_config": full}
        if source == "field":
            return {"from_field": full}
        return {"from_env": full}

    def env_from_block(self, meta, children):
        result = []
        for c in children:
            if isinstance(c, n.EnvFromSpec):
                result.append(c)
            elif isinstance(c, tuple) and len(c) == 2:
                result.append(n.EnvFromSpec(source=c[1], kind=c[0]))
        return tuple(result)

    def env_from_entry(self, meta, children):
        return (_str(children[0]), _str(children[2]))

    # Resources
    def resources_block(self, meta, children):
        requests = limits = None
        req_fields: dict = {}
        for c in children:
            if isinstance(c, tuple) and len(c) == 2:
                k, v = c
                if k == "requests":
                    requests = v
                elif k == "limits":
                    limits = v
                elif k == "cpu":
                    req_fields["cpu"] = v
                elif k == "memory":
                    req_fields["memory"] = v
        if req_fields:
            requests = n.ResourceMap(
                cpu=req_fields.get("cpu"), memory=req_fields.get("memory")
            )
        return n.ResourcesSpec(requests=requests, limits=limits)

    def resource_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if isinstance(children[0], Token) and children[0].type in (
            "REQUESTS",
            "LIMITS",
        ):
            # no-colon block form: LIMITS resource_map
            rmap = next((c for c in children if isinstance(c, n.ResourceMap)), None)
            key = "requests" if children[0].type == "REQUESTS" else "limits"
            return (key, rmap)
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def resource_map(self, meta, children):
        cpu = memory = None
        for c in children:
            if isinstance(c, tuple) and len(c) == 2:
                if c[0] == "cpu":
                    cpu = c[1]
                elif c[0] == "memory":
                    memory = c[1]
        return n.ResourceMap(cpu=cpu, memory=memory)

    def resource_entry(self, meta, children):
        return (_name(children[0]), children[2])

    def resource_cpu(self, meta, children):
        return ("cpu", children[2])

    def resource_memory(self, meta, children):
        return ("memory", children[2])

    # Health / probes
    def health_spec(self, meta, children):
        if isinstance(children[0], n.HealthSpec):
            return children[0]
        return n.HealthSpec(**_pick(children[0], n.HealthSpec))

    def health_shorthand(self, meta, children):
        kind = _str(children[0])
        path = _str(children[2])
        fields = (
            children[4] if len(children) > 4 and isinstance(children[4], dict) else {}
        )
        picked = _pick(fields, n.HealthSpec)
        # An explicit `path:` in the object overrides the shorthand URL path;
        # otherwise fall back to the shorthand so the kwargs never collide.
        if "path" in picked:
            path = picked.pop("path")
        return n.HealthSpec(kind=kind, path=path, **picked)

    def health_object(self, meta, children):
        fields = self._body_dict(children)
        # _body_dict aggregates a `port:` field into the "ports" tuple (for
        # service port lists); a health check has a single scalar port, so
        # recover it back onto the "port" key.
        if "ports" in fields and "port" not in fields and len(fields["ports"]) == 1:
            fields["port"] = fields["ports"][0]
            del fields["ports"]
        return fields

    def health_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def probes_block(self, meta, children):
        liveness = readiness = startup = None
        for c in children:
            if isinstance(c, tuple) and len(c) == 2:
                if c[0] == "liveness":
                    liveness = c[1]
                elif c[0] == "readiness":
                    readiness = c[1]
                elif c[0] == "startup":
                    startup = c[1]
        return n.ProbesSpec(liveness=liveness, readiness=readiness, startup=startup)

    def probe_entry(self, meta, children):
        key = _str(children[0])
        health = None
        for c in children:
            if isinstance(c, n.HealthSpec):
                health = c
                break
        if len(children) >= 3 and children[1].type == "COLON":
            health = children[2]
        return (key, health)

    # Volumes
    def volume_list(self, meta, children):
        return tuple(c for c in children if isinstance(c, n.VolumeSpec))

    def volume_block(self, meta, children):
        return tuple(c for c in children if isinstance(c, n.VolumeSpec))

    def volume_spec(self, meta, children):
        # named form: IDENTIFIER COLON LBRACE fields RBRACE
        if (
            len(children) >= 2
            and isinstance(children[0], Token)
            and children[0].type == "IDENTIFIER"
        ):
            has_colon = any(getattr(c, "type", "") == "COLON" for c in children)
            has_lbrace = any(getattr(c, "type", "") == "LBRACE" for c in children)
            if has_colon and has_lbrace:
                name = _str(children[0])
                fields = self._body_dict(children)
                vol = self._volume_from_fields(fields)
                return n.VolumeSpec(
                    name=name,
                    mount_path=vol.mount_path,
                    host_path=vol.host_path,
                    claim=vol.claim,
                    storage_class=vol.storage_class,
                    size=vol.size,
                    read_only=vol.read_only,
                )
        if isinstance(children[0], dict):
            return self._volume_from_fields(children[0])
        if isinstance(children[0], n.VolumeSpec):
            return children[0]
        # bare block / list element: gather field tuples
        fields = self._body_dict(children)
        return self._volume_from_fields(fields)

    def volume_field(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def _volume_from_fields(self, fields: dict) -> n.VolumeSpec:
        return n.VolumeSpec(
            name=_lit(fields.get("name", "")),
            mount_path=_lit(fields.get("mount_path")),
            host_path=_lit(fields.get("host_path")),
            claim=_lit(fields.get("claim")),
            storage_class=_lit(fields.get("sc")),
            size=fields.get("size"),
            read_only=bool(fields.get("read_only", False)),
        )

    # Strategy
    def strategy_block(self, meta, children):
        if isinstance(children[0], str):
            return n.StrategySpec(type=children[0])
        fields = children[0]
        return n.StrategySpec(
            type=_lit(fields.get("type", "rolling")),
            **_pick(fields, n.StrategySpec, exclude=("type",)),
        )

    def strategy_name(self, meta, children):
        return _str(children[0])

    def strategy_object(self, meta, children):
        return self._body_dict(children)

    def strategy_item(self, meta, children):
        if children and getattr(children[0], "type", None) == "CANARY":
            fields = {
                c[0]: c[1] for c in children if isinstance(c, tuple) and len(c) == 2
            }
            step = n.CanaryStep(
                weight=_int(fields["weight"]) if "weight" in fields else None,
                steps=_int(fields["steps"]) if "steps" in fields else None,
                traffic=_num(fields["traffic"]) if "traffic" in fields else None,
            )
            return ("canary", (step,))
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def canary_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    # Security
    def security_block(self, meta, children):
        fields = self._body_dict(children)
        return n.SecuritySpec(
            user=_int(fields["user"]) if "user" in fields else None,
            group=_int(fields["group"]) if "group" in fields else None,
            capabilities=_lit_list(fields.get("capabilities")),
            seccomp=_lit(fields.get("seccomp")),
            selinux=fields.get("selinux"),
            read_only_root_filesystem=_bool(
                fields.get("read_only_root_filesystem", False)
            ),
            privileged=_bool(fields.get("privileged", False)),
        )

    def security_item(self, meta, children):
        # SELINUX block: collect child selinux field tuples into a SelinuxSpec
        if children and isinstance(children[0], Token) and children[0].type == "SELINUX":
            fields = {}
            for c in children:
                if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                    fields[c[0]] = c[1]
            spec = n.SelinuxSpec(
                level=_lit(fields.get("level")),
                role=_lit(fields.get("role")),
                type=_lit(fields.get("type")),
            )
            return ("selinux", spec)
        for c in children:
            if isinstance(c, n.SelinuxSpec):
                return ("selinux", c)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def selinux_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    # Lifecycle
    def lifecycle_block(self, meta, children):
        fields = self._body_dict(children)
        return n.LifecycleSpec(
            post_start=fields.get("postStart"), pre_stop=fields.get("preStop")
        )

    def lifecycle_item(self, meta, children):
        # POST_START/PRE_STOP hook_block  ->  ("postStart"/"preStop", HookSpec)
        if isinstance(children[0], Token) and children[0].type in (
            "POST_START",
            "PRE_STOP",
        ):
            hook = next((c for c in children if isinstance(c, n.HookSpec)), None)
            key = "postStart" if children[0].type == "POST_START" else "preStop"
            return (key, hook)
        if isinstance(children[0], n.HookSpec):
            return ("preStop", children[0])
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def hook_block(self, meta, children):
        fields = self._body_dict(children)
        if "exec" in fields:
            return n.HookSpec(kind="exec", command=_lit_list(fields["exec"]))
        if "command" in fields:
            return n.HookSpec(kind="exec", command=_lit_list(fields["command"]))
        if "http" in fields:
            return n.HookSpec(kind="http", url=_lit(fields["http"]))
        return n.HookSpec()

    def hook_field(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    # Schedule
    def schedule_block(self, meta, children):
        default = None
        slots = []
        for c in children:
            if isinstance(c, tuple) and len(c) == 2:
                key, config = c
                if key == "default":
                    default = config
                else:
                    slots.append(n.ScheduleSlot(cron=key, config=config))
        return n.ScheduleSpec(default=default, slots=tuple(slots))

    # Autoscale
    # Network Policy
    def network_policy_block(self, meta, children):
        allow_from = ()
        deny_from = ()
        allow_egress = ()
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                if c[0] == "allow_from":
                    allow_from = _lit_list(c[1])
                elif c[0] == "deny_from":
                    deny_from = _lit_list(c[1])
                elif c[0] == "allow_egress":
                    allow_egress = _lit_list(c[1])
        return n.NetworkPolicySpec(
            allow_from=allow_from, deny_from=deny_from, allow_egress=allow_egress
        )

    def network_policy_item(self, meta, children):
        if isinstance(children[0], Token) and children[0].type in (
            "ALLOW_FROM", "DENY_FROM", "ALLOW_EGRESS",
        ):
            key = _name(children[0])
            val = children[1]
            if isinstance(val, Token) and val.type == "COLON":
                val = children[2] if len(children) > 2 else None
            return (key, val)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        return children[0]

    # Topology spread
    def topology_block(self, meta, children):
        spread_by = "zone"
        max_skew = 1
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                if c[0] == "spread_by":
                    spread_by = _lit(c[1]) or "zone"
                elif c[0] == "max_skew":
                    max_skew = _int(c[1])
        return n.TopologySpec(spread_by=spread_by, max_skew=max_skew)

    def topology_item(self, meta, children):
        if isinstance(children[0], Token) and children[0].type in ("SPREAD_BY", "MAX_SKEW"):
            key = _name(children[0])
            val = children[1]
            if isinstance(val, Token) and val.type == "COLON":
                val = children[2] if len(children) > 2 else None
            return (key, val)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        return children[0]

    # Pod affinity / anti-affinity
    def affinity_block(self, meta, children):
        prefer_same: tuple = ()
        avoid_same: tuple = ()
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                if c[0] == "prefer_same":
                    prefer_same = _lit_list(c[1])
                elif c[0] == "avoid_same":
                    avoid_same = _lit_list(c[1])
        return n.AffinitySpec(prefer_same=prefer_same, avoid_same=avoid_same)

    def affinity_item(self, meta, children):
        if isinstance(children[0], Token) and children[0].type in (
            "PREFER_SAME",
            "AVOID_SAME",
        ):
            key = _name(children[0])
            val = children[1]
            if isinstance(val, Token) and val.type == "COLON":
                val = children[2] if len(children) > 2 else None
            return (key, val)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        return children[0]

    def autoscale_block(self, meta, children):
        fields = {}
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                fields[c[0]] = c[1]
        return n.AutoscaleSpec(
            min_replicas=_int(fields["min"]) if "min" in fields else 1,
            max_replicas=_int(fields["max"]) if "max" in fields else 10,
            target_cpu=_int(fields["target_cpu"]) if "target_cpu" in fields else 70,
            target_memory=_int(fields["target_memory"]) if "target_memory" in fields else None,
            scale_up_delay=fields.get("scale_up_delay"),
            scale_down_delay=fields.get("scale_down_delay"),
        )

    def autoscale_item(self, meta, children):
        # children = [KEY, (COLON)?, value]
        if isinstance(children[0], Token) and children[0].type in (
            "MIN", "MAX", "TARGET_CPU", "TARGET_MEMORY",
            "SCALE_UP_DELAY", "SCALE_DOWN_DELAY",
        ):
            key = _name(children[0])
            val = children[1]
            if isinstance(val, Token) and val.type == "COLON":
                val = children[2] if len(children) > 2 else None
            return (key, val)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        return children[0]

    # Disruption
    def disruption_block(self, meta, children):
        fields = {}
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                fields[c[0]] = c[1]
        min_av = fields.get("min_available")
        max_un = fields.get("max_unavailable")
        if isinstance(min_av, n.Literal):
            min_av = min_av.value
        if isinstance(max_un, n.Literal):
            max_un = max_un.value
        return n.DisruptionSpec(min_available=min_av, max_unavailable=max_un)

    def disruption_item(self, meta, children):
        if isinstance(children[0], Token) and children[0].type in (
            "MIN_AVAILABLE", "MAX_UNAVAILABLE",
        ):
            key = _name(children[0])
            val = children[1]
            if isinstance(val, Token) and val.type == "COLON":
                val = children[2] if len(children) > 2 else None
            return (key, val)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        return children[0]

    def schedule_item(self, meta, children):
        # children = [schedule_key, (COLON)?, schedule_config]
        key = None
        config = None
        for c in children:
            if isinstance(c, n.ScheduleConfig):
                config = c
            elif isinstance(c, Token) and c.type != "COLON":
                key = c.value.strip('"')
            elif isinstance(c, n.Literal):
                key = str(c.value)
        if config is None:
            config = n.ScheduleConfig()
        return (key or "default", config)

    def schedule_config(self, meta, children):
        replicas = 2
        cpu = None
        memory = None
        for c in children:
            if isinstance(c, tuple) and len(c) == 2:
                k, v = c
                if k == "replicas":
                    replicas = _int(v)
                elif k == "cpu":
                    cpu = v
                elif k == "memory":
                    memory = v
            elif isinstance(c, n.Literal) and isinstance(c.value, (int, float)):
                replicas = int(c.value)
        return n.ScheduleConfig(replicas=replicas, cpu=cpu, memory=memory)

    def schedule_field(self, meta, children):
        # forms: "replicas" NUMBER  |  "replicas" ":" NUMBER  |  "cpu" ":"? resource_value  # noqa: E501
        if isinstance(children[0], Token) and children[0].type in ("REPLICAS", "CPU", "MEMORY"):  # noqa: E501
            key = _name(children[0])
            val = children[1]
            if isinstance(val, Token) and val.type == "COLON":
                val = children[2] if len(children) > 2 else None
            return (key, val)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    # ------------------------------------------------------------------ #
    # Database / Cache / Queue / Storage / Network
    # ------------------------------------------------------------------ #

    def database_def(self, meta, children):
        name = _str(children[1])
        body = children[3] if len(children) > 3 else {}
        return n.DatabaseDef(
            name=name,
            users=tuple(body.get("users", ())),
            backup=body.get("backup"),
            **_pick(body, n.DatabaseDef, exclude=("users", "backup")),
            location=_loc(meta),
        )

    def database_body(self, meta, children):
        return self._body_dict(children)

    def database_item(self, meta, children):
        users = [c for c in children if isinstance(c, n.DbUserSpec)]
        if users:
            return ("users", tuple(users))
        for c in children:
            if isinstance(c, n.BackupSpec):
                return ("backup", c)
            if isinstance(c, tuple) and len(c) == 2:
                return c
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return None

    def backup_block(self, meta, children):
        fields = self._body_dict(children)
        return n.BackupSpec(
            enabled=bool(fields.get("enabled", False)),
            schedule=_lit(fields.get("schedule")),
            retention=fields.get("retention"),
            storage=_lit(fields.get("storage")),
        )

    def backup_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def db_user(self, meta, children):
        return n.DbUserSpec(name=_str(children[0]), password=_lit(children[2]))

    def cache_def(self, meta, children):
        name = _str(children[1])
        body = children[3] if len(children) > 3 else {}
        return n.CacheDef(name=name, **_pick(body, n.CacheDef), location=_loc(meta))

    def cache_body(self, meta, children):
        return self._body_dict(children)

    def cache_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def queue_def(self, meta, children):
        name = _str(children[1])
        body = children[3] if len(children) > 3 else {}
        return n.QueueDef(
            name=name,
            topics=tuple(body.get("topics", ())),
            users=tuple(body.get("users", ())),
            config=body.get("config"),
            **_pick(body, n.QueueDef, exclude=("topics", "users", "config")),
            location=_loc(meta),
        )

    def queue_body(self, meta, children):
        return self._body_dict(children)

    def queue_item(self, meta, children):
        if children and children[0].type == "CONFIG":
            fields = tuple(c for c in children if isinstance(c, tuple) and len(c) == 2)
            return ("config", n.QueueConfigSpec(entries=fields))
        topics = [c for c in children if isinstance(c, n.TopicSpec)]
        if topics:
            return ("topics", tuple(topics))
        users = [c for c in children if isinstance(c, n.MqUserSpec)]
        if users:
            return ("users", tuple(users))
        for c in children:
            if isinstance(c, n.QueueConfigSpec):
                return ("config", c)
            if isinstance(c, tuple) and len(c) == 2:
                return c
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return None

    def topic_spec(self, meta, children):
        name = _str(children[0])
        fields = self._body_dict(children[3:])
        return n.TopicSpec(
            name=name,
            partitions=_int(fields["partitions"]) if "partitions" in fields else None,
            replication=(
                _int(fields["replication"]) if "replication" in fields else None
            ),
            retention=fields.get("retention"),
        )

    def topic_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def queue_config_item(self, meta, children):
        return (_str(children[0]), children[2])

    def mq_user(self, meta, children):
        return n.MqUserSpec(name=_str(children[0]), password=_lit(children[2]))

    def storage_def(self, meta, children):
        name = _str(children[1])
        body = children[3] if len(children) > 3 else {}
        return n.StorageDef(
            name=name,
            lifecycle=body.get("lifecycle"),
            **_pick(body, n.StorageDef, exclude=("lifecycle",)),
            location=_loc(meta),
        )

    def storage_body(self, meta, children):
        return self._body_dict(children)

    def storage_item(self, meta, children):
        if isinstance(children[0], n.StorageLifecycle):
            return ("lifecycle", children[0])
        for c in children:
            if isinstance(c, n.StorageLifecycle):
                return ("lifecycle", c)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def storage_lifecycle(self, meta, children):
        fields = self._body_dict(children)
        return n.StorageLifecycle(
            retention=fields.get("retention"),
            prefix=_lit(fields.get("prefix")),
            transition=_lit(fields.get("transition")),
            expiration=fields.get("expiration"),
        )

    def lifecycle_field(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def network_def(self, meta, children):
        name = _str(children[1])
        body = children[3] if len(children) > 3 else {}
        return n.NetworkDef(
            name=name,
            subnets=tuple(body.get("subnets", ())),
            policy=body.get("policy"),
            **_pick(body, n.NetworkDef, exclude=("subnets", "policy")),
            location=_loc(meta),
        )

    def network_body(self, meta, children):
        return self._body_dict(children)

    def network_item(self, meta, children):
        subnets = [c for c in children if isinstance(c, n.SubnetSpec)]
        if subnets:
            return ("subnets", tuple(subnets))
        rules = [c for c in children if isinstance(c, n.PolicyRule)]
        if rules:
            return ("policy", n.NetworkPolicy(rules=tuple(rules)))
        for c in children:
            if isinstance(c, n.NetworkPolicy):
                return ("policy", c)
            if isinstance(c, tuple) and len(c) == 2:
                return c
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return None

    def subnet_spec(self, meta, children):
        name = _str(children[0])
        fields = self._body_dict(children[3:])
        return n.SubnetSpec(
            name=name, cidr=_lit(fields.get("cidr")), az=_lit(fields.get("az"))
        )

    def subnet_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def policy_rule(self, meta, children):
        name = _str(children[0])
        fields = self._body_dict(children[3:])
        return n.PolicyRule(
            name=name,
            from_=_lit(fields.get("from")),
            to=_lit(fields.get("to")),
            ports=_int_list(fields.get("ports")),
            selector=self._pairs(fields.get("selector")),
        )

    def rule_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    # ------------------------------------------------------------------ #
    # Secret & Config
    # ------------------------------------------------------------------ #

    def secret_def(self, meta, children):
        name = _str(children[1])
        body = children[3] if len(children) > 3 else []
        entries = tuple(body) if isinstance(body, (list, tuple)) else ()
        return n.SecretDef(name=name, entries=entries, location=_loc(meta))

    def secret_body(self, meta, children):
        return [c for c in children if isinstance(c, n.SecretEntry)]

    def secret_item(self, meta, children):
        entry_name = _str(children[0])
        value = children[2]
        if isinstance(value, tuple):
            if value[0] == "value":
                return n.SecretEntry(name=entry_name, value=_lit(value[1]))
            _, src, sname, key = value
            kw: dict = {"key": key}
            mapping = {
                "env": "from_env",
                "file": "from_file",
                "vault": "from_vault",
                "aws": "from_aws",
                "gcp": "from_gcp",
            }
            kw[mapping.get(src, "value")] = sname
            return n.SecretEntry(name=entry_name, **kw)
        return n.SecretEntry(name=entry_name, value=_lit(value))

    def secret_value(self, meta, children):
        if len(children) == 1:
            return ("value", children[0])
        source = _str(children[1])
        name = _str(children[2])
        key = _str(children[4]) if len(children) >= 5 else None
        return ("ref", source, name, key)

    def config_def(self, meta, children):
        name = _str(children[1])
        body = children[3] if len(children) > 3 else []
        entries = tuple(body) if isinstance(body, (list, tuple)) else ()
        return n.ConfigDef(name=name, entries=entries, location=_loc(meta))

    def config_body(self, meta, children):
        return [c for c in children if isinstance(c, n.ConfigEntry)]

    def config_item(self, meta, children):
        entry_name = _str(children[0])
        value = children[2]
        return n.ConfigEntry(name=entry_name, value=value)

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #

    def pipeline_def(self, meta, children):
        name = _str(children[1])
        body = children[3] if len(children) > 3 else {}
        return n.PipelineDef(
            name=name,
            trigger=body.get("trigger"),
            stages=tuple(body.get("stages", ())),
            artifacts=body.get("artifacts"),
            cache=body.get("cache"),
            concurrency=body.get("concurrency"),
            **_pick(
                body,
                n.PipelineDef,
                exclude=("trigger", "stages", "artifacts", "cache", "concurrency"),
            ),
            location=_loc(meta),
        )

    def pipeline_body(self, meta, children):
        return self._body_dict(children)

    def pipeline_item(self, meta, children):
        if children and children[0].type == "TRIGGER":
            fields = {
                c[0]: c[1] for c in children if isinstance(c, tuple) and len(c) == 2
            }
            return (
                "trigger",
                n.TriggerSpec(
                    branches=_lit_list(fields.get("branches")),
                    tags=_lit_list(fields.get("tags")),
                    paths=_lit_list(fields.get("paths")),
                    schedule=_lit(fields.get("schedule")),
                    manual=bool(fields.get("manual", False)),
                    events=_lit_list(fields.get("events")),
                ),
            )
        if children and children[0].type == "ARTIFACTS":
            fields = {
                c[0]: c[1] for c in children if isinstance(c, tuple) and len(c) == 2
            }
            return (
                "artifacts",
                n.ArtifactsSpec(
                    upload=_lit_list(fields.get("upload")),
                    download=_lit_list(fields.get("download")),
                ),
            )
        if children and children[0].type == "PIPELINE_CACHE":
            fields = {
                c[0]: c[1] for c in children if isinstance(c, tuple) and len(c) == 2
            }
            return (
                "cache",
                n.PipelineCacheSpec(
                    path=_lit(fields.get("path")),
                    key=_lit(fields.get("key")),
                    restore_keys=_lit_list(fields.get("restore_keys")),
                ),
            )
        if children and children[0].type == "CONCURRENCY":
            fields = {
                c[0]: c[1] for c in children if isinstance(c, tuple) and len(c) == 2
            }
            return (
                "concurrency",
                n.ConcurrencySpec(
                    group=_lit(fields.get("group")),
                    cancel_in_progress=bool(fields.get("cancel_in_progress", False)),
                ),
            )
        stages = [c for c in children if isinstance(c, n.StageSpec)]
        if stages:
            return ("stages", tuple(stages))
        for c in children:
            if isinstance(c, tuple) and len(c) == 2:
                return c
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return None

    def trigger_field(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def stage_spec(self, meta, children):
        name = _str(children[0])
        fields = self._body_dict(children[3:])
        steps = tuple(fields.get("steps", ()))
        parallel = tuple(fields.get("parallel", ()))
        return n.StageSpec(
            name=name,
            image=_lit(fields.get("image")),
            runs_on=_lit(fields.get("runs_on")),
            needs=_lit_list(fields.get("needs")),
            condition=_lit(fields.get("if")),
            env=self._pairs(fields.get("env")),
            timeout=fields.get("timeout"),
            matrix=fields.get("matrix"),
            parallel=parallel,
            steps=steps,
        )

    def stage_item(self, meta, children):
        steps = [c for c in children if isinstance(c, n.StepSpec)]
        if steps:
            return ("steps", tuple(steps))
        parallel = [c for c in children if isinstance(c, n.StageSpec)]
        if parallel:
            return ("parallel", tuple(parallel))
        if children and children[0].type == "MATRIX":
            dims = tuple(c for c in children if isinstance(c, tuple) and len(c) == 2)
            return ("matrix", n.MatrixSpec(dimensions=dims))
        for c in children:
            if isinstance(c, tuple) and len(c) == 2:
                return c
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return None

    def step_spec(self, meta, children):
        name = _str(children[0])
        body = children[2]
        if isinstance(body, dict):
            fields = body
            return n.StepSpec(
                name=name,
                run=_lit(fields.get("run")),
                uses=_lit(fields.get("uses")),
                with_args=self._pairs(fields.get("with")),
                condition=_lit(fields.get("if")),
                continue_on_error=bool(fields.get("continue_on_error", False)),
                timeout=fields.get("timeout"),
                env=self._pairs(fields.get("env")),
            )
        return n.StepSpec(name=name, run=_lit(body))

    @staticmethod
    def _pairs(v):
        """Coerce a Map (or tuple) into a tuple of (key, value) string pairs."""
        if isinstance(v, n.Map):
            return tuple((_lit(e.key), _lit(e.value)) for e in v.entries)
        if isinstance(v, (list, tuple)):
            return tuple(v)
        return ()

    def step_body(self, meta, children):
        # expression form -> return expression; block form -> build dict of fields
        fields = {}
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                fields[c[0]] = c[1]
        return fields if fields else children[0]

    def step_field(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def artifact_field(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def cache_field(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def conc_field(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def matrix_item(self, meta, children):
        return (_str(children[0]), tuple(_lit_list(children[2])))

    # ------------------------------------------------------------------ #
    # Environment & Cluster
    # ------------------------------------------------------------------ #

    def environment_def(self, meta, children):
        name = _str(children[1])
        extends = None
        if len(children) >= 5 and isinstance(children[2], Token) and children[2].type == "EXTENDS":
            extends = _str(children[3])
            body = children[5] if len(children) > 5 else {}
        else:
            body = children[3] if len(children) > 3 else {}
        return n.EnvironmentDef(
            name=name, extends=extends, **_pick(body, n.EnvironmentDef),
            location=_loc(meta)
        )

    def env_def_body(self, meta, children):
        return self._body_dict(children)

    def env_def_item(self, meta, children):
        if isinstance(children[0], n.ResourcesSpec):
            return ("resources", children[0])
        for c in children:
            if isinstance(c, n.QuotaSpec):
                return ("quotas", c)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def quota_block(self, meta, children):
        max_cpu = None
        max_memory = None
        max_pods = None
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str):
                if c[0] == "max_cpu":
                    max_cpu = c[1]
                elif c[0] == "max_memory":
                    max_memory = c[1]
                elif c[0] == "max_pods":
                    max_pods = _int(c[1])
        return n.QuotaSpec(max_cpu=max_cpu, max_memory=max_memory, max_pods=max_pods)

    def quota_item(self, meta, children):
        if isinstance(children[0], Token) and children[0].type in (
            "MAX_CPU", "MAX_MEMORY", "MAX_PODS",
        ):
            key = _name(children[0])
            val = children[1]
            if isinstance(val, Token) and val.type == "COLON":
                val = children[2] if len(children) > 2 else None
            return (key, val)
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        return children[0]

    def cluster_def(self, meta, children):
        name = _str(children[1])
        body = children[3] if len(children) > 3 else {}
        return n.ClusterDef(
            name=name,
            nodes=tuple(body.get("nodes", ())),
            networking=body.get("networking"),
            iam=body.get("iam"),
            **_pick(body, n.ClusterDef, exclude=("nodes", "networking", "iam")),
            location=_loc(meta),
        )

    def cluster_body(self, meta, children):
        return self._body_dict(children)

    def cluster_item(self, meta, children):
        if children and children[0].type == "NETWORKING":
            fields = {
                c[0]: c[1] for c in children if isinstance(c, tuple) and len(c) == 2
            }
            return (
                "networking",
                n.ClusterNetworkingSpec(
                    cidr=_lit(fields.get("cidr")), vpc=_lit(fields.get("vpc"))
                ),
            )
        if children and children[0].type == "IAM":
            sa = role = None
            for c in children:
                if isinstance(c, tuple) and len(c) == 2 and c[0] == "serviceAccount":
                    sa = c[1]
                elif isinstance(c, tuple) and len(c) == 2 and c[0] == "role":
                    role = c[1]
            return ("iam", n.ClusterIamSpec(service_account=sa, role=role))
        nodes = [c for c in children if isinstance(c, n.NodePoolSpec)]
        if nodes:
            return ("nodes", tuple(nodes))
        for c in children:
            if isinstance(c, n.ClusterNetworkingSpec):
                return ("networking", c)
            if isinstance(c, n.ClusterIamSpec):
                return ("iam", c)
            if isinstance(c, tuple) and len(c) == 2:
                return c
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return None

    def node_pool(self, meta, children):
        name = _str(children[0])
        fields = self._body_dict(children[3:])
        return n.NodePoolSpec(
            name=name,
            machine_type=_lit(fields.get("machine_type")),
            min=_int(fields["min"]) if "min" in fields else None,
            max=_int(fields["max"]) if "max" in fields else None,
            labels=self._pairs(fields.get("labels")),
        )

    def node_item(self, meta, children):
        if children and children[0].type == "MACHINE":
            # children = [MACHINE, TYPE, COLON, expr]
            return ("machine_type", children[3])
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return None

    def cluster_net_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def sa_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def role_item(self, meta, children):
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return children[0]

    def cluster_iam_item(self, meta, children):
        if children and children[0].type == "SA":
            fields = {
                c[0]: c[1] for c in children if isinstance(c, tuple) and len(c) == 2
            }
            return (
                "serviceAccount",
                n.ServiceAccountSpec(
                    name=_lit(fields.get("name")),
                    policy=self._pairs(fields.get("policy")),
                ),
            )
        if children and children[0].type == "ROLE":
            fields = {
                c[0]: c[1] for c in children if isinstance(c, tuple) and len(c) == 2
            }
            return (
                "role",
                n.RoleSpec(
                    name=_lit(fields.get("name")),
                    actions=_lit_list(fields.get("actions")),
                    resources=_lit_list(fields.get("resources")),
                ),
            )
        if isinstance(children[0], tuple) and len(children[0]) == 2:
            return children[0]
        if len(children) >= 3 and children[1].type == "COLON":
            return self._field(children)
        return None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _binary(self, children):
        if len(children) == 1:
            return children[0]
        result = children[0]
        for i in range(1, len(children), 2):
            result = n.BinaryOp(
                left=result, operator=_str(children[i]), right=children[i + 1]
            )
        return result

    @staticmethod
    def _parse_template(raw: str):
        parts: list = []
        buf = []
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch == "{":
                if buf:
                    parts.append("".join(buf))
                    buf = []
                depth = 1
                expr = []
                i += 1
                while i < len(raw) and depth > 0:
                    c = raw[i]
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    expr.append(c)
                    i += 1
                parts.append(("expr", "".join(expr)))
                i += 1
            else:
                buf.append(ch)
                i += 1
        if buf:
            parts.append("".join(buf))
        return parts


#: Fields that are enums/strings: an Identifier here is coerced to its name,
#: while expression-like fields (e.g. ``image``) keep the Identifier so the
#: semantic validator can flag undefined variables.
_STR_COERCE = {
    "type",
    "provider",
    "policy",
    "storage_class",
    "access_mode",
    "seccomp",
    "protocol",
    "region",
    "bucket",
    "namespace",
    "schedule",
    "host",
    "domain",
    "path",
    "uses",
    "run",
    "machine_type",
    "cache_policy",
}


def _pick(fields: dict, cls, exclude: tuple = ()) -> dict:
    """Return only fields that are dataclass attributes of ``cls`` (minus name),
    coercing ``Literal`` values to Python primitives for scalar-typed fields."""
    import dataclasses
    import typing

    prim = {int: "int", float: "float", str: "str", bool: "bool"}
    hints = typing.get_type_hints(cls)
    result = {}
    for f in dataclasses.fields(cls):
        if f.name == "name" or f.name in exclude or f.name not in fields:
            continue
        value = fields[f.name]
        t = hints.get(f.name, str)
        base = None
        origin = getattr(t, "__origin__", None)
        if origin is not None:  # Union/Optional
            for arg in getattr(t, "__args__", ()):
                if arg in prim:
                    base = arg
        elif t in prim:
            base = t
        if isinstance(value, n.Literal) and base in prim:
            result[f.name] = {
                int: int,
                float: float,
                str: str,
                bool: bool,
            }[
                base
            ](value.value)
        elif isinstance(value, n.Identifier) and base is bool:
            result[f.name] = value.name == "true"
        elif isinstance(value, n.Identifier) and base is str and f.name in _STR_COERCE:
            result[f.name] = value.name
        elif isinstance(value, n.Duration) and base is float:
            result[f.name] = value.to_seconds()
        elif isinstance(value, n.Duration) and "ResourceValue" in str(t):
            result[f.name] = n.ResourceValue(value=value.value, unit=value.unit)
        elif isinstance(value, n.List) and _is_str_tuple(t):
            result[f.name] = tuple(_lit(x) for x in value.items)
        elif isinstance(value, n.Map) and _is_str_pair_tuple(t):
            result[f.name] = tuple((_lit(e.key), _lit(e.value)) for e in value.entries)
        else:
            result[f.name] = value
    return result


def _is_str_tuple(t) -> bool:
    """True if the annotation is Tuple[str, ...]."""
    origin = getattr(t, "__origin__", None)
    if origin not in (tuple,):
        return False
    args = getattr(t, "__args__", ())
    return len(args) == 2 and args[1] is Ellipsis and args[0] is str  # type: ignore[misc]


def _is_str_pair_tuple(t) -> bool:
    """True if the annotation is Tuple[Tuple[str, str], ...]."""
    origin = getattr(t, "__origin__", None)
    if origin not in (tuple,):
        return False
    args = getattr(t, "__args__", ())
    if len(args) != 2 or args[1] is not Ellipsis:  # type: ignore[misc]
        return False
    inner = args[0]  # type: ignore[misc]
    iorigin = getattr(inner, "__origin__", None)
    if iorigin is not tuple:
        return False
    iargs = getattr(inner, "__args__", ())
    if len(iargs) != 2:
        return False
    return iargs[0] is str and iargs[1] is str  # type: ignore[misc]
