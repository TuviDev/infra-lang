"""Completeness suite: exercises every field of every AST node.

Each test class targets one node class and asserts on each of its fields so
that no AST field can regress without a failing test.
"""

from __future__ import annotations

import pytest

from infra import parse
from infra.parser import ast_nodes as n


def first_stmt(source: str):
    """Return the first user (non-prelude) statement."""
    program = parse(source)
    for s in program.statements:
        if getattr(getattr(s, "location", None), "file", "") == "<prelude>":
            continue
        return s
    return program.statements[-1]


# ─── Literals ───────────────────────────────────────────
class TestLiteralNode:
    def test_string(self):
        assert first_stmt('let x = "hello"').value == n.Literal(value="hello")

    def test_int(self):
        node = first_stmt("let x = 42").value
        assert isinstance(node.value, int) and node.value == 42

    def test_float(self):
        node = first_stmt("let x = 3.14").value
        assert isinstance(node.value, float) and node.value == pytest.approx(3.14)

    def test_bool_true(self):
        assert first_stmt("let x = true").value.value is True

    def test_bool_false(self):
        assert first_stmt("let x = false").value.value is False

    def test_null(self):
        assert first_stmt("let x = null").value.value is None

    def test_frozen(self):
        lit = n.Literal(value="t")
        with pytest.raises((AttributeError, TypeError)):
            lit.value = "other"  # type: ignore[misc]

    def test_location_defaults_none(self):
        assert n.Literal(value=42).location is None


class TestDurationNode:
    @pytest.mark.parametrize("expr,unit,secs", [
        ("1ms", "ms", 0.001), ("30s", "s", 30.0), ("5min", "min", 300.0),
        ("2h", "h", 7200.0), ("7d", "d", 604800.0), ("1w", "w", 604800.0),
    ])
    def test_to_seconds(self, expr, unit, secs):
        d = first_stmt(f"let t = {expr}").value
        assert isinstance(d, n.Duration)
        assert d.unit == unit
        assert d.to_seconds() == pytest.approx(secs)


class TestResourceValueNode:
    @pytest.mark.parametrize("expr,val,unit,k8s", [
        ("500m", 500, "m", "500m"), ("128Mi", 128, "Mi", "128Mi"),
        ("2Gi", 2, "Gi", "2Gi"), ("1Ti", 1, "Ti", "1Ti"),
        ("256Ki", 256, "Ki", "256Ki"), ("1cores", 1, "cores", "1cores"),
    ])
    def test_units(self, expr, val, unit, k8s):
        svc = first_stmt(
            f'service s {{ image: "nginx:1.0" resources {{ cpu: {expr} }} }}'
        )
        cpu = svc.resources.requests.cpu
        assert isinstance(cpu, n.ResourceValue)
        assert cpu.value == val and cpu.unit == unit
        assert cpu.to_kubernetes() == k8s

    def test_to_bytes(self):
        assert n.ResourceValue(1, "Gi").to_bytes() == 1024**3
        assert n.ResourceValue(128, "Mi").to_bytes() == 128 * 1024**2


class TestIdentifierNode:
    def test_name(self):
        ident = n.Identifier(name="x")
        assert ident.name == "x"

    def test_frozen(self):
        ident = n.Identifier(name="x")
        with pytest.raises((AttributeError, TypeError)):
            ident.name = "y"  # type: ignore[misc]


class TestBinaryOpNode:
    @pytest.mark.parametrize("op", ["+", "-", "*", "/", "==", "!=", "<", ">", "<=", ">=", "&&", "||"])
    def test_operators(self, op):
        node = first_stmt(f"let x = 1 {op} 2")
        assert isinstance(node.value, n.BinaryOp)
        assert node.value.operator == op

    def test_left_right(self):
        expr = first_stmt("let x = 1 + 2").value
        assert expr.left.value == 1 and expr.right.value == 2


class TestUnaryOpNode:
    def test_operator_operand(self):
        expr = first_stmt("let x = !flag").value
        assert isinstance(expr, n.UnaryOp)
        assert expr.operator == "!"
        assert isinstance(expr.operand, n.Identifier)


class TestCallNode:
    def test_callee_args_kwargs(self):
        expr = first_stmt("let x = foo(a, key = b)").value
        assert isinstance(expr, n.Call)
        assert expr.callee.name == "foo"
        assert len(expr.args) == 1
        assert expr.kwargs == (("key", n.Identifier(name="b", location=expr.kwargs[0][1].location)),)


class TestIndexNode:
    def test_obj_index(self):
        expr = first_stmt("let x = arr[0]").value
        assert isinstance(expr, n.Index)
        assert expr.obj.name == "arr"
        assert expr.index.value == 0


class TestAttributeNode:
    def test_obj_attr(self):
        expr = first_stmt("let x = obj.field").value
        assert isinstance(expr, n.Attribute)
        assert expr.obj.name == "obj"
        assert expr.attr == "field"


class TestListNode:
    def test_items(self):
        lst = first_stmt('let x = ["a", "b", "c"]').value
        assert isinstance(lst, n.List)
        assert len(lst.items) == 3

    def test_empty(self):
        assert len(first_stmt("let x = []").value.items) == 0

    def test_nested(self):
        assert len(first_stmt("let x = [[1, 2]]").value.items) == 1


class TestMapNode:
    def test_entries(self):
        m = first_stmt("let x = {a: 1, b: 2}").value
        assert isinstance(m, n.Map)
        assert len(m.entries) == 2

    def test_entry_key_value(self):
        m = first_stmt('let x = {key: "value"}').value
        entry = m.entries[0]
        assert isinstance(entry, n.MapEntry)
        assert entry.key.name == "key"
        assert entry.value.value == "value"


class TestTemplateStringNode:
    def test_parts(self):
        ts = first_stmt("let x = `a {b} c`").value
        assert isinstance(ts, n.TemplateString)
        assert len(ts.parts) >= 3


class TestIfExprNode:
    def test_fields(self):
        e = first_stmt("let x = if a then b else c").value
        assert isinstance(e, n.IfExpr)
        assert e.condition.name == "a"
        assert e.then_branch.name == "b"
        assert e.else_branch.name == "c"


class TestMatchNode:
    def test_subject_arms(self):
        e = first_stmt('let m = match s { 1 -> "a" _ -> "b" }').value
        assert isinstance(e, n.MatchExpr)
        assert e.subject.name == "s"
        assert len(e.arms) == 2
        assert isinstance(e.arms[0], n.MatchArm)


# ─── Declarations ───────────────────────────────────────
class TestVariableDecl:
    def test_let(self):
        v = first_stmt("let x = 1")
        assert isinstance(v, n.VariableDecl)
        assert v.name == "x" and v.const is False

    def test_const(self):
        v = first_stmt("const X = 1")
        assert v.const is True


class TestImport:
    def test_plain(self):
        program = parse('import "./x.infra"')
        imp = program.imports[0]
        assert isinstance(imp, n.Import)
        assert imp.path == "./x.infra" and imp.alias is None and imp.names == ()

    def test_alias(self):
        imp = parse('import "./x.infra" as lib').imports[0]
        assert imp.alias == "lib"


class TestDecorator:
    def test_args(self):
        svc = first_stmt('@replicas(3)\nservice s { image: "x" }')
        assert svc.decorators[0].name == "replicas"
        assert len(svc.decorators[0].args) == 1


# ─── ServiceDef all fields ──────────────────────────────
class TestServiceDefAllFields:
    def test_fields(self):
        svc = first_stmt(
            'service api { image: "img:1" replicas: 3 port 8080 '
            'env { A: "b" } resources { requests { cpu: 100m } } '
            'health http("/h") volumes { v: { mountPath: "/d" } } '
            'depends: ["db"] labels: { app: "x" } '
            'security { user: 1000 } strategy: rolling '
            'lifecycle { preStop { exec: ["sleep","5"] } } '
            'ingress { host: "a.com" } expose: true '
            'schedule { "0 9 * * 1-5": replicas 3 } }'
        )
        assert svc.name == "api"
        assert svc.image == "img:1"
        assert svc.replicas == 3
        assert len(svc.ports) == 1
        assert len(svc.env) == 1
        assert svc.resources is not None
        assert svc.health is not None
        assert len(svc.volumes) == 1
        assert svc.depends == ("db",)
        assert svc.labels == (("app", "x"),)
        assert svc.security is not None
        assert svc.strategy is not None
        assert svc.lifecycle is not None
        assert svc.ingress is not None
        assert svc.expose is True
        assert svc.schedule is not None


# ─── DatabaseDef all fields ─────────────────────────────
class TestDatabaseDefAllFields:
    def test_fields(self):
        db = first_stmt(
            'database db { type: postgres version: "15" replicas: 3 ha: true '
            'ssl: true storage: 20Gi '
            'backup { enabled: true schedule: "0 2 * * *" } '
            'users { app: "pw" } }'
        )
        assert db.type == "postgres"
        assert db.version == "15"
        assert db.replicas == 3
        assert db.ha is True
        assert db.ssl is True
        assert db.storage is not None and db.storage.unit == "Gi"
        assert db.backup is not None
        assert len(db.users) == 1


# ─── Cache / Queue / Storage / Network ──────────────────
class TestCacheDef:
    def test_fields(self):
        c = first_stmt('cache c { type: redis maxmemory: 512Mi policy: "x" persistence: true replicas: 2 }')
        assert c.type == "redis" and c.maxmemory is not None and c.policy == "x"
        assert c.persistence is True and c.replicas == 2


class TestQueueDef:
    def test_fields(self):
        q = first_stmt('queue q { type: rabbitmq topics { t: { partitions: 3 } } users { u: "p" } }')
        assert q.type == "rabbitmq"
        assert len(q.topics) == 1 and q.topics[0].partitions == 3
        assert len(q.users) == 1


class TestStorageDef:
    def test_fields(self):
        s = first_stmt('storage s { type: s3 bucket: "b" region: "r" lifecycle { expiration: 30d } }')
        assert s.type == "s3" and s.bucket == "b" and s.region == "r"
        assert s.lifecycle is not None


class TestNetworkDef:
    def test_fields(self):
        net = first_stmt('network n { cidr: "10.0.0.0/16" subnets { a: { cidr: "1.1.1.1" } } policy { r: { from: "x" } } }')
        assert net.cidr == "10.0.0.0/16"
        assert len(net.subnets) == 1
        assert net.policy is not None


# ─── Secret / Config ────────────────────────────────────
class TestSecretDef:
    def test_entries(self):
        s = first_stmt('secret s { a: from env "A" b: "plain" }')
        assert isinstance(s, n.SecretDef)
        assert len(s.entries) == 2


class TestConfigDef:
    def test_entries(self):
        c = first_stmt('config c { log_level: "info" }')
        assert isinstance(c, n.ConfigDef)
        assert len(c.entries) == 1


# ─── Pipeline ───────────────────────────────────────────
class TestPipelineDef:
    def test_fields(self):
        p = first_stmt(
            'pipeline ci { trigger { branches: ["main"] } '
            'stages { t: { runsOn: "ubuntu" needs: [] steps { s: { run: "x" } } } } '
            'artifacts { upload: ["dist/"] } '
            'cache { path: "/c" } '
            'concurrency { group: "g" } }'
        )
        assert p.name == "ci"
        assert p.trigger is not None and p.trigger.branches == ("main",)
        assert len(p.stages) == 1
        assert p.artifacts is not None
        assert p.cache is not None
        assert p.concurrency is not None


# ─── Environment / Cluster ──────────────────────────────
class TestEnvironmentDef:
    def test_fields(self):
        e = first_stmt('environment dev { provider: aws region: "eu" namespace: "ns" labels: { a: "b" } }')
        assert e.provider == "aws" and e.region == "eu" and e.namespace == "ns"
        assert e.labels == (("a", "b"),)


class TestClusterDef:
    def test_fields(self):
        c = first_stmt('cluster c { provider: aws nodes { w: { machine type: "t3" min: 1 max: 5 } } }')
        assert c.provider == "aws"
        assert len(c.nodes) == 1
        assert c.nodes[0].machine_type == "t3"
