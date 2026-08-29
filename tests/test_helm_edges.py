"""Edge-case tests for the Helm backend (v0.5.3).

Covers previously-missed helpers: single-resource entry points, image
splitting, resource-quantity rendering, storage fallback, CRD value
coercion, the dependency port map and ``_lit``.
"""

from __future__ import annotations

from infra.backends.helm import HelmBackend, _crd_value, _dep_port_map, _lit
from infra.parser import ast_nodes as n
from infra.parser import parse

_SVC = 'service api { image: "app:1" }'
_DB = 'database db { engine: "postgres" storage: 20Gi }'


class TestSingleResourceEntryPoints:
    def test_get_version(self) -> None:
        assert HelmBackend().get_version() == "1.0"

    def test_compile_service_returns_deployment_yaml(self) -> None:
        prog = parse(_SVC)
        out = HelmBackend().compile_service(prog.statements[0])
        assert "kind: Deployment" in out or "Deployment" in out

    def test_compile_database_returns_statefulset_yaml(self) -> None:
        prog = parse(_DB)
        out = HelmBackend().compile_database(prog.statements[0])
        assert "StatefulSet" in out


class TestSplitImage:
    def test_tagged_image(self) -> None:
        assert HelmBackend._split_image("app:1.2") == {
            "repository": "app",
            "tag": "1.2",
        }

    def test_untagged_image_defaults_latest(self) -> None:
        assert HelmBackend._split_image("nginx") == {
            "repository": "nginx",
            "tag": "latest",
        }

    def test_registry_with_port_and_namespace(self) -> None:
        out = HelmBackend._split_image("reg.io:5000/ns/app")
        assert out["repository"] == "reg.io"
        assert out["tag"] == "5000/ns/app"


class TestLitFallback:
    def test_literal_unwrapped(self) -> None:
        assert _lit(n.Literal("x")) == "x"

    def test_non_literal_returns_none(self) -> None:
        assert _lit(n.Identifier("x")) is None
        assert _lit(None) is None


class TestDepPortMap:
    def test_statements_without_name_are_skipped(self) -> None:
        prog = parse(_SVC)
        # Import statements have no ``name`` attribute at all.
        prog = type(prog)(
            statements=prog.statements + (n.Import(path="x.infra", names=()),),
            environments=prog.environments,
        )
        ports = _dep_port_map(prog)
        assert ports == {"api": 80}

    def test_service_with_explicit_ports(self) -> None:
        prog = parse('service api { image: "a" port 9090:8080 }')
        assert _dep_port_map(prog)["api"] == 9090

    def test_database_cache_queue_defaults(self) -> None:
        prog = parse(
            'database db { engine: "postgres" }\n'
            "cache redis {}\n"
            "queue q { type: kafka }\n"
        )
        ports = _dep_port_map(prog)
        assert set(ports) == {"db", "redis", "q"}
        assert all(isinstance(p, int) for p in ports.values())


class TestValuesYamlBranches:
    def test_service_build_without_image(self) -> None:
        prog = parse('service api { build { context: "." } }')
        values = HelmBackend()._values_yaml(prog)
        assert "built-from-dockerfile" in values

    def test_depends_on_rendered(self) -> None:
        prog = parse(
            _DB + "\n" + 'service api { image: "a" port 8080 depends_on: [db] }'
        )
        values = HelmBackend()._values_yaml(prog)
        assert "dependsOn" in values

    def test_resources_requests_and_limits(self) -> None:
        prog = parse(
            'service api { image: "a" resources { '
            "requests { cpu: 100m, memory: 128Mi } "
            "limits { cpu: 2 } } }"
        )
        values = HelmBackend()._values_yaml(prog)
        assert "requests:" in values
        assert "cpu: 100m" in values
        assert "memory: 128Mi" in values
        assert "limits:" in values
        assert "cpu: '2'" in values

    def test_storage_default_when_missing(self) -> None:
        prog = parse('database db { engine: "postgres" }')
        values = HelmBackend()._values_yaml(prog)
        assert "storage" in values

    def test_statement_without_name_skipped_in_values(self) -> None:
        prog = parse(_SVC)
        prog = type(prog)(
            statements=prog.statements + (n.Import(path="x.infra", names=()),),
            environments=prog.environments,
        )
        values = HelmBackend()._values_yaml(prog)
        assert "service:" in values
        assert "api:" in values


class TestCrdValueCoercion:
    def test_literal_and_identifier(self) -> None:
        assert _crd_value(n.Literal(3)) == 3
        assert _crd_value(n.Identifier("REPLICAS")) == "REPLICAS"

    def test_list_recursion(self) -> None:
        out = _crd_value(n.List(items=(n.Literal(1), n.Literal("a"))))
        assert out == [1, "a"]

    def test_map_key_variants(self) -> None:
        value = n.Map(
            entries=(
                n.MapEntry(key=n.Identifier("replicas"), value=n.Literal(2)),
                n.MapEntry(key=n.Literal("str-key"), value=n.Literal(1)),
                n.MapEntry(key=n.Literal(7), value=n.Literal(0)),
            )
        )
        out = _crd_value(value)
        assert out == {"replicas": 2, "str-key": 1, "7": 0}

    def test_template_string_interpolation(self) -> None:
        ts = n.TemplateString(parts=("http://", n.Identifier("host"), ":9090"))
        assert _crd_value(ts) == "http://host:9090"

    def test_unknown_node_falls_back_to_str(self) -> None:
        out = _crd_value(n.BinaryOp(n.Literal(1), "+", n.Literal(2)))
        assert isinstance(out, str)
        assert "BinaryOp" in out or "1" in out
