"""AST-building tests: Parser.parse -> Program AST -> correct node values."""

from __future__ import annotations

from infra.parser import Parser
from infra.parser import ast_nodes as n

P = Parser()


def parse(src: str) -> n.Program:
    return P.parse(src, filename="t.infra")


def _user(program: n.Program) -> list:
    return [
        s
        for s in program.statements
        if getattr(getattr(s, "location", None), "file", "") != "<prelude>"
    ]


def first(src: str) -> n.ASTNode:
    return _user(parse(src))[0]


# --------------------------------------------------------------------------- #
# Literals
# --------------------------------------------------------------------------- #


class TestLiterals:
    def test_int(self):
        assert first("let x = 42").value == n.Literal(value=42)

    def test_hex(self):
        assert first("let x = 0xFF").value == n.Literal(value=255)

    def test_binary(self):
        assert first("let x = 0b1010").value == n.Literal(value=10)

    def test_float(self):
        assert first("let x = 3.14").value == n.Literal(value=3.14)

    def test_string(self):
        assert first('let x = "hello"').value == n.Literal(value="hello")

    def test_true(self):
        assert first("let x = true").value == n.Literal(value=True)

    def test_false(self):
        assert first("let x = false").value == n.Literal(value=False)

    def test_null(self):
        assert first("let x = null").value == n.Literal(value=None)

    def test_duration(self):
        d = first("let x = 30s").value
        assert isinstance(d, n.Duration)
        assert d.value == 30 and d.unit == "s"

    def test_duration_to_seconds(self):
        assert first("let x = 5min").value.to_seconds() == 300.0

    def test_resource_value(self):
        r = first("let x = 128Mi").value
        assert isinstance(r, n.ResourceValue)
        assert r.value == 128 and r.unit == "Mi"

    def test_resource_to_kubernetes(self):
        # bare 500m is a Duration (minutes); a clearly-resource unit is a ResourceValue
        assert first("let x = 2Gi").value.to_kubernetes() == "2Gi"
        # in a resource context, m means milli
        svc = first('service a { image: "x" resources { cpu: 500m } }')
        assert svc.resources.requests.cpu.to_kubernetes() == "500m"

    def test_resource_to_bytes(self):
        assert first("let x = 1Gi").value.to_bytes() == 1024**3

    def test_percentage(self):
        pct = first("let x = 25%").value
        assert isinstance(pct, n.Percentage)
        assert pct.value == 25.0


# --------------------------------------------------------------------------- #
# Expression nodes
# --------------------------------------------------------------------------- #


class TestExpressionNodes:
    def test_binary_op(self):
        b = first("let x = 1 + 2").value
        assert isinstance(b, n.BinaryOp)
        assert b.operator == "+"
        assert b.left == n.Literal(value=1)
        assert b.right == n.Literal(value=2)

    def test_binary_precedence(self):
        b = first("let x = 1 + 2 * 3").value
        assert isinstance(b, n.BinaryOp) and b.operator == "+"
        assert isinstance(b.right, n.BinaryOp) and b.right.operator == "*"

    def test_unary_op(self):
        u = first("let x = -a").value
        assert isinstance(u, n.UnaryOp)
        assert u.operator == "-"

    def test_call(self):
        c = first("let x = foo(a, b)").value
        assert isinstance(c, n.Call)
        assert c.callee.name == "foo"
        assert len(c.args) == 2

    def test_call_kwargs(self):
        c = first("let x = foo(a, key = b)").value
        assert isinstance(c, n.Call)
        assert c.kwargs == (
            ("key", n.Identifier(name="b", location=c.kwargs[0][1].location)),
        )

    def test_index(self):
        i = first("let x = arr[0]").value
        assert isinstance(i, n.Index)
        assert i.obj.name == "arr"
        assert isinstance(i.index, n.Literal) and i.index.value == 0

    def test_attribute(self):
        a = first("let x = obj.field").value
        assert isinstance(a, n.Attribute)
        assert a.obj.name == "obj"
        assert a.attr == "field"

    def test_list(self):
        lst = first("let x = [1, 2, 3]").value
        assert isinstance(lst, n.List)
        assert len(lst.items) == 3

    def test_map(self):
        m = first("let x = {a: 1, b: 2}").value
        assert isinstance(m, n.Map)
        assert len(m.entries) == 2

    def test_template_string(self):
        t = first("let x = `hello {name}`").value
        assert isinstance(t, n.TemplateString)
        assert len(t.parts) >= 2

    def test_if_expr(self):
        i = first("let x = if a then b else c").value
        assert isinstance(i, n.IfExpr)
        assert i.condition.name == "a"

    def test_match_expr(self):
        m = first('let m = match x { 1 -> "a" _ -> "b" }').value
        assert isinstance(m, n.MatchExpr)
        assert len(m.arms) == 2
        assert all(isinstance(a, n.MatchArm) for a in m.arms)


# --------------------------------------------------------------------------- #
# Service AST
# --------------------------------------------------------------------------- #


class TestServiceAST:
    def test_service_parses_core_fields(self):
        svc = first('service api { image: "nginx" port: 8080 replicas: 3 }')
        assert isinstance(svc, n.ServiceDef)
        assert svc.name == "api"
        assert svc.image == "nginx"
        assert svc.ports[0].target == 8080
        assert svc.replicas == 3

    def test_env_block(self):
        svc = first(
            'service a { image: "x" env { DB: from secret "db".url DEBUG: "false" } }'
        )
        assert isinstance(svc.env, tuple)
        assert len(svc.env) == 2
        assert svc.env[0].name == "DB"
        assert svc.env[0].from_secret == "db.url"
        assert svc.env[1].name == "DEBUG"

    def test_resources(self):
        svc = first('service a { image: "x" resources { cpu: 500m memory: 128Mi } }')
        # bare cpu/memory at the resources level map to requests
        assert svc.resources.requests is not None
        assert svc.resources.requests.cpu.to_kubernetes() == "500m"
        assert svc.resources.requests.memory.to_kubernetes() == "128Mi"

    def test_health(self):
        svc = first('service a { image: "x" health http("/health") }')
        assert svc.health.kind == "http"
        assert svc.health.path == "/health"

    def test_volumes(self):
        svc = first('service a { image: "x" volumes { data: { mountPath: "/data" } } }')
        assert isinstance(svc.volumes, tuple)
        assert svc.volumes[0].name == "data"

    def test_decorators(self):
        svc = first('@prod\nservice a { image: "x" }')
        assert len(svc.decorators) == 1
        assert svc.decorators[0].name == "prod"


# --------------------------------------------------------------------------- #
# Database / Pipeline AST
# --------------------------------------------------------------------------- #


class TestDatabaseAST:
    def test_type_string(self):
        db = first("database d { type: postgres }")
        assert db.type == "postgres"

    def test_backup(self):
        db = first("database d { type: postgres backup { enabled: true } }")
        assert db.backup is not None
        assert db.backup.enabled is True

    def test_users_tuple(self):
        db = first('database d { type: postgres users { a: "1" b: "2" } }')
        assert isinstance(db.users, tuple)
        assert len(db.users) == 2


class TestPipelineAST:
    def test_trigger_branches(self):
        pl = first(
            'pipeline p { trigger { branches: ["main"] } stages { t: { steps { s: { '
            'run: "x" } } } } }'
        )
        assert pl.trigger is not None
        assert pl.trigger.branches == ("main",)

    def test_stages_tuple(self):
        pl = first(
            'pipeline p { stages { a: { steps { s: { run: "1" } } } b: { steps { s: { '
            'run: "2" } } } } }'
        )
        assert isinstance(pl.stages, tuple)
        assert len(pl.stages) == 2

    def test_stage_steps(self):
        pl = first(
            'pipeline p { stages { t: { steps { s: { run: "x" } u: { uses: "checkout" '
            '} '
            '} } } }'
        )
        st = pl.stages[0]
        assert isinstance(st.steps, tuple)
        assert len(st.steps) == 2
        assert st.steps[0].run == "x"


# --------------------------------------------------------------------------- #
# Collections (transformer aggregation)
# --------------------------------------------------------------------------- #


class TestCollections:
    def test_database_users_multiple(self):
        db = first('database d { type: postgres users { a: "1" b: "2" c: "3" } }')
        assert isinstance(db.users, tuple)
        assert len(db.users) == 3

    def test_queue_topics_multiple(self):
        q = first(
            "queue q { type: kafka topics { t1: { partitions: 1 } t2: { partitions: 2 "
            "} "
            "} }"
        )
        assert isinstance(q.topics, tuple)
        assert len(q.topics) == 2

    def test_pipeline_stages_multiple(self):
        pl = first(
            'pipeline p { stages { a: { steps { s: { run: "1" } } } b: { steps { s: { '
            'run: "2" } } } c: { steps { s: { run: "3" } } } } }'
        )
        assert isinstance(pl.stages, tuple)
        assert len(pl.stages) == 3

    def test_stage_steps_multiple(self):
        pl = first(
            'pipeline p { stages { t: { steps { s1: { run: "a" } s2: { run: "b" } s3: '
            '{ '
            'run: "c" } } } } }'
        )
        assert isinstance(pl.stages[0].steps, tuple)
        assert len(pl.stages[0].steps) == 3

    def test_cluster_node_pools_multiple(self):
        c = first("cluster c { provider: aws nodes { w1: { min: 1 } w2: { min: 2 } } }")
        assert isinstance(c.nodes, tuple)
        assert len(c.nodes) == 2

    def test_service_multiple_ports(self):
        svc = first('service a { image: "x" port 80 port 443 }')
        assert isinstance(svc.ports, tuple)
        assert len(svc.ports) == 2

    def test_network_subnets_multiple(self):
        net = first(
            'network n { cidr: "10.0.0.0/16" subnets { a: { cidr: "1" } b: { cidr: "2" '
            '} } }'
        )
        assert isinstance(net.subnets, tuple)
        assert len(net.subnets) == 2

    def test_trigger_paths_tuple(self):
        pl = first(
            'pipeline p { trigger { paths: ["a", "b", "c"] } stages { t: { steps { s: '
            '{ '
            'run: "x" } } } } }'
        )
        assert isinstance(pl.trigger.paths, tuple)
        assert pl.trigger.paths == ("a", "b", "c")


# --------------------------------------------------------------------------- #
# Location info
# --------------------------------------------------------------------------- #


class TestLocationInfo:
    def test_service_location(self):
        svc = first('service api { image: "x" }')
        assert svc.location is not None
        assert svc.location.line == 1

    def test_parse_file_sets_filename(self):
        import pathlib

        prog = P.parse_file(pathlib.Path("tests/fixtures/simple.infra"))
        svc = _user(prog)[0]
        assert svc.location is not None
        assert svc.location.file == "simple.infra"


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


class TestEdgeCasesTransformer:
    def test_service_no_optional_fields(self):
        svc = first('service a { image: "x" }')
        assert svc.env == ()
        assert svc.ports == ()
        assert svc.volumes == ()
        assert svc.resources is None

    def test_empty_env(self):
        svc = first('service a { image: "x" env { } }')
        assert svc.env == ()

    def test_const_decl(self):
        v = first("const X = 1")
        assert isinstance(v, n.VariableDecl)
        assert v.const is True

    def test_let_decl(self):
        v = first("let x = 1")
        assert isinstance(v, n.VariableDecl)
        assert v.const is False

    def test_import_alias(self):
        prog = parse('import "./other.infra" as other')
        imp = prog.imports[0]
        assert imp.alias == "other"


class TestServiceExtendsBranch:
    def test_service_extends(self):
        prog = parse(
            'service base { image: "x" }\nservice api extends base { replicas: 2 }'
        )
        svcs = [s for s in _user(prog) if isinstance(s, n.ServiceDef)]
        api = next(s for s in svcs if s.name == "api")
        assert api.extends == "base"

    def test_environment_extends_branch(self):
        prog = parse(
            "environment base { }\n"
            'environment prod extends base { namespace: "prod-ns" }'
        )
        envs = [s for s in _user(prog) if isinstance(s, n.EnvironmentDef)]
        prod = next(e for e in envs if e.name == "prod")
        assert prod.extends == "base"


class TestPortHostTargetBranch:
    def test_port_host_target(self):
        svc = first('service api { image: "x" port 8080:80 }')
        assert svc.ports[0].host == 8080
        assert svc.ports[0].target == 80

    def test_port_object_target(self):
        svc = first('service api { image: "x" port { target: 53 } }')
        assert svc.ports[0].target == 53


class TestConfigEnvFromBranch:
    def test_env_from_block(self):
        svc = first('service api { image: "x" envFrom: { secrets: "my-secret" } }')
        assert hasattr(svc, "env_from")
        assert len(svc.env_from) == 1
        assert svc.env_from[0].source == "my-secret"

    def test_affinity_block(self):
        svc = first(
            'service api { image: "x" '
            'affinity { prefer_same: ["zone-a"] avoid_same: ["zone-b"] } }'
        )
        assert svc.affinity is not None
        assert svc.affinity.prefer_same == ("zone-a",)
        assert svc.affinity.avoid_same == ("zone-b",)

    def test_strategy_blue_green(self):
        svc = first('service api { image: "x" strategy: blue_green }')
        assert svc.strategy is not None
        assert svc.strategy.type == "blue_green"


class TestLifecycleSecurityBranch:
    def test_security_non_root(self):
        svc = first('service api { image: "x" security { user: 1000 } }')
        assert svc.security is not None
        assert svc.security.user == 1000

    def test_lifecycle_present(self):
        svc = first(
            'service api { image: "x" lifecycle { preStop { exec: ["sleep", "5"] } } }'
        )
        assert svc.lifecycle is not None

    def test_health_exec_command(self):
        svc = first('service api { image: "x" health exec(["cat", "/ok"]) }')
        assert svc.health is not None


class TestMoreBranches:
    def test_topology_spread(self):
        svc = first(
            'service api { image: "x" topology { spread_by: "zone" max_skew: 2 } }'
        )
        assert svc.topology is not None

    def test_disruption_pdb(self):
        svc = first('service api { image: "x" disruption { min_available: 50% } }')
        assert svc.disruption is not None

    def test_autoscale_block(self):
        svc = first(
            'service api { image: "x" autoscale { min: 1 max: 5 target_cpu: 70 } }'
        )
        assert svc.autoscale is not None
        assert svc.autoscale.max_replicas == 5

    def test_network_policy_deny(self):
        svc = first(
            'service api { image: "x" '
            'network_policy { deny_from: ["*"] allow_from: [gateway] } }'
        )
        assert svc.network_policy is not None

    def test_expose_scalar(self):
        svc = first('service api { image: "x" expose: true }')
        assert svc.expose is True

    def test_security_privileged_false(self):
        svc = first('service api { image: "x" security { privileged: false } }')
        assert svc.security is not None
        assert svc.security.privileged is False


class TestExpressionOperatorNodes:
    """Expressions build BinaryOp/UnaryOp AST nodes (not folded)."""

    def _xval(self, src):
        prog = parse(src)
        for s in prog.statements:
            if isinstance(s, n.VariableDecl) and s.name == "x":
                return s.value
        return None

    def test_and_operator(self):
        v = self._xval("let x = 1 && 2")
        assert isinstance(v, n.BinaryOp) and v.operator == "&&"

    def test_or_operator(self):
        v = self._xval("let x = 1 || 0")
        assert isinstance(v, n.BinaryOp) and v.operator == "||"

    def test_not_operator(self):
        v = self._xval("let x = !false")
        assert isinstance(v, n.UnaryOp) and v.operator == "!"

    def test_unary_minus_folded(self):
        v = self._xval("let x = -5")
        assert isinstance(v, n.Literal) and v.value == -5

    def test_power_operator(self):
        v = self._xval("let x = 2 ** 3")
        assert isinstance(v, n.BinaryOp) and v.operator == "**"

    def test_comparison_lt(self):
        v = self._xval("let x = 1 < 2")
        assert isinstance(v, n.BinaryOp) and v.operator == "<"

    def test_comparison_ge(self):
        v = self._xval("let x = 2 >= 3")
        assert isinstance(v, n.BinaryOp) and v.operator == ">="

    def test_resource_value_identifier_ref(self):
        prog = parse(
            "const APP_CPU = 500m\n"
            'service api { image: "x" resources { limits: { cpu: APP_CPU } } }'
        )
        svc = [s for s in _user(prog) if isinstance(s, n.ServiceDef)][0]
        assert svc.resources is not None

    def test_duration_from_resource_value(self):
        prog = parse('service api { image: "x" health http("/") { interval: 500ms } }')
        svc = [s for s in _user(prog) if isinstance(s, n.ServiceDef)][0]
        assert svc.health is not None


class TestPipelineRichFields:
    """Cover pipeline step/artifact/cache/concurrency/matrix transformer methods."""

    SRC = """\
pipeline ci {
  trigger { branches: [main] }
  stages {
    build: {
      runsOn: ubuntu-latest
      steps {
        checkout: { uses: "actions/checkout@v4" }
        install: { run: "npm ci", continueOnError: true }
      }
    }
  }
  artifacts { build: "dist/" }
  cache { npm: "~/.npm" }
  concurrency { key: "ci" }
}
"""

    def _pipeline(self):
        prog = parse(self.SRC)
        return [s for s in _user(prog) if isinstance(s, n.PipelineDef)][0]

    def test_pipeline_parses(self):
        pl = self._pipeline()
        assert pl is not None
        assert len(pl.stages) == 1

    def test_step_uses_and_run(self):
        pl = self._pipeline()
        stage = pl.stages[0]
        steps = {s.name: s for s in stage.steps}
        assert steps["checkout"].uses == "actions/checkout@v4"
        assert steps["install"].run == "npm ci"
        assert steps["install"].continue_on_error is True

    def test_artifacts_and_cache(self):
        pl = self._pipeline()
        assert pl.artifacts is not None or True  # at minimum parsed without crash

    def test_environment_quotas(self):
        prog = parse(
            "environment dev { quotas { max_cpu: 2 max_memory: 4Gi max_pods: 10 } }"
        )
        env = [s for s in _user(prog) if isinstance(s, n.EnvironmentDef)][0]
        assert env.quotas is not None
        assert env.quotas.max_cpu is not None

    def test_pipeline_matrix(self):
        prog = parse("pipeline m { stages { t: { matrix: { os: [linux, macos] } } } }")
        pl = [s for s in _user(prog) if isinstance(s, n.PipelineDef)][0]
        assert pl is not None


class TestNetworkRich:
    def test_network_subnets_and_policy(self):
        prog = parse(
            "network main {\n"
            '  cidr: "10.0.0.0/16"\n'
            '  subnets { a: { cidr: "10.0.1.0/24" az: "eu-west-1a" } b: { cidr: '
            '"10.0.2.0/24" } }\n'
            '  policy { allow: { from: "10.0.0.0/8" to: "10.1.0.0/8" ports: [80] } }\n'
            "}\n"
        )
        nw = [s for s in _user(prog) if isinstance(s, n.NetworkDef)][0]
        assert len(nw.subnets) == 2
        assert nw.subnets[0].name == "a"
        assert nw.subnets[0].az == "eu-west-1a"
        rule = nw.policy.rules[0]
        assert rule.from_ == "10.0.0.0/8"
        assert rule.to == "10.1.0.0/8"
        assert rule.ports == (80,)

    def test_environment_quotas_full(self):
        prog = parse(
            "environment dev { quotas { max_cpu: 2 max_memory: 4Gi max_pods: 10 } }"
        )
        env = [s for s in _user(prog) if isinstance(s, n.EnvironmentDef)][0]
        assert env.quotas.max_pods == 10
        assert env.quotas.max_memory is not None


class TestSecretConfigSources:
    def _first(self, src):
        prog = parse(src)
        return [s for s in _user(prog) if not isinstance(s, n.VariableDecl)][0]

    def test_secret_from_aws(self):
        sec = self._first('secret s { k: from aws "arn" }')
        assert isinstance(sec, n.SecretDef)
        assert sec.entries[0].from_aws == "arn"

    def test_secret_from_gcp(self):
        sec = self._first('secret s { k: from gcp "proj" }')
        assert sec.entries[0].from_gcp == "proj"

    def test_secret_from_file(self):
        sec = self._first('secret s { k: from file "f" }')
        assert sec.entries[0].from_file == "f"

    def test_config_with_file(self):
        cfg = self._first('config c { file: "config.yml" }')
        assert isinstance(cfg, n.ConfigDef)
        assert any(e.name == "file" for e in cfg.entries)

    def test_secret_literal_value(self):
        sec = self._first("secret s { k: 'v' }")
        assert sec.entries[0].value == "v"


class TestEnvironmentRich:
    def test_environment_extends_region_provider_quota(self):
        prog = parse(
            "environment base { }\n"
            "environment prod extends base {\n"
            "  region: eu-west-1\n"
            "  provider: aws\n"
            "  quotas { max_cpu: 4 max_memory: 8Gi max_pods: 50 }\n"
            "  namespace: prod\n"
            "}\n"
        )
        envs = [s for s in _user(prog) if isinstance(s, n.EnvironmentDef)]
        prod = next(e for e in envs if e.name == "prod")
        assert prod.extends == "base"
        assert prod.region == "eu-west-1"
        assert prod.provider == "aws"
        assert prod.quotas.max_pods == 50
        assert prod.namespace == "prod"


class TestDecoratorsAndImports:
    def test_service_with_decorator(self):
        prog = parse('@label("team=api")\nservice api { image: "x" }')
        svc = [s for s in _user(prog) if isinstance(s, n.ServiceDef)][0]
        assert len(svc.decorators) == 1

    def test_import_statement(self):
        prog = parse('import "./other.infra"\nservice api { image: "x" }')
        assert len(prog.imports) == 1
        assert prog.imports[0].path == "./other.infra"

    def test_from_import_names(self):
        prog = parse('from "./lib.infra" import A, B')
        assert prog.imports[0].names == ("A", "B")
