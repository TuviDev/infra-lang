"""Tests for the v0.4.5 ``depends_on`` service-dependency contract.

Covers grammar/AST parsing (bare and bracketed forms), co-existence with
the legacy ``depends`` list, validator rules (DEPENDENCY_NOT_FOUND and
DEPENDENCY_CYCLE), formatter round-trips, ``extends`` inheritance and
``infra graph`` edge rendering.
"""

from __future__ import annotations

import pytest

from infra.analyzer.validator import SemanticValidator
from infra.cli.printer import format_source
from infra.errors.exceptions import InfraParseError
from infra.parser import Parser
from infra.parser import ast_nodes as n

P = Parser()


def parse(src: str) -> n.Program:
    return P.parse(src, filename="d.infra")


def services(program: n.Program) -> dict:
    return {s.name: s for s in program.statements if isinstance(s, n.ServiceDef)}


def validate(src: str):
    return SemanticValidator().validate(parse(src))


def codes(result) -> list:
    return [e.code for e in result.errors]


class TestGrammar:
    def test_bracketed_form(self):
        svc = services(
            parse(
                'service api { image: "app" depends_on: [db, redis] }\n'
                'service db { image: "pg" }\n'
                'service redis { image: "redis" }'
            )
        )["api"]
        assert svc.depends_on == ("db", "redis")

    def test_bare_single_form(self):
        src = 'service api { image: "app" depends_on: db }\nservice db { image: "pg" }'
        svc = services(parse(src))["api"]
        assert svc.depends_on == ("db",)

    def test_bare_comma_form(self):
        svc = services(
            parse(
                'service api { image: "app" depends_on: db, redis }\n'
                'service db { image: "pg" }\n'
                'service redis { image: "redis" }'
            )
        )["api"]
        assert svc.depends_on == ("db", "redis")

    def test_absent_by_default(self):
        svc = services(parse('service api { image: "app" }'))["api"]
        assert svc.depends == ()
        assert svc.depends_on == ()
        assert svc.dependencies == ()

    def test_coexists_with_legacy_depends(self):
        src = (
            'service api { image: "app" depends: ["q"] depends_on: [db] }\n'
            'service db { image: "pg" }\n'
            'service q { image: "q" }'
        )
        svc = services(parse(src))["api"]
        assert svc.depends == ("q",)
        assert svc.depends_on == ("db",)
        # merged view used by graph/backends, de-duplicated, order-stable
        assert svc.dependencies == ("q", "db")

    def test_dependencies_dedup(self):
        src = (
            'service api { image: "app" depends: ["db"] depends_on: [db] }\n'
            'service db { image: "pg" }'
        )
        svc = services(parse(src))["api"]
        assert svc.dependencies == ("db",)

    def test_duplicate_names_preserved_in_field(self):
        # the raw field intentionally keeps what the user wrote; the merged
        # `dependencies` property de-duplicates
        src = (
            'service api { image: "app" depends_on: [db, db] }\n'
            'service db { image: "pg" }'
        )
        svc = services(parse(src))["api"]
        assert svc.depends_on == ("db", "db")
        assert svc.dependencies == ("db",)

    def test_bare_form_does_not_swallow_next_item(self):
        # `depends_on: db` followed by another service item requires the
        # bracketed form — a bare list cannot be unambiguously terminated
        with pytest.raises(InfraParseError):
            parse('service api { image: "app" depends_on: db, replicas: 2 }')

    def test_depends_on_inside_multiline_block(self):
        src = (
            "service api {\n"
            '    image: "app"\n'
            "    port 8080\n"
            "    depends_on: [db, redis]\n"
            "    replicas: 2\n"
            "}\n"
            'service db { image: "pg" }\n'
            'service redis { image: "redis" }\n'
        )
        svc = services(parse(src))["api"]
        assert svc.depends_on == ("db", "redis")
        assert svc.replicas == 2


class TestValidation:
    def test_undeclared_target_is_error(self):
        result = validate('service api { image: "app" depends_on: [ghost] }')
        assert not result.is_valid
        assert "DEPENDENCY_NOT_FOUND" in codes(result)

    def test_undeclared_target_message_and_hint(self):
        result = validate('service api { image: "app" depends_on: [ghost] }')
        err = next(e for e in result.errors if e.code == "DEPENDENCY_NOT_FOUND")
        assert "'ghost'" in err.message
        assert err.hint == "Declare service 'ghost' or fix spelling in depends_on"

    def test_forward_reference_is_allowed(self):
        result = validate(
            'service api { image: "app" depends_on: [db] }\n'
            'service db { image: "pg" }'
        )
        assert "DEPENDENCY_NOT_FOUND" not in codes(result)

    def test_database_and_cache_targets_are_allowed(self):
        result = validate(
            'service api { image: "app" depends_on: [pg, cache] }\n'
            "database pg { type: postgres }\n"
            "cache cache { type: redis }"
        )
        assert "DEPENDENCY_NOT_FOUND" not in codes(result)

    def test_queue_target_is_allowed(self):
        result = validate(
            'service api { image: "app" depends_on: [jobs] }\n'
            "queue jobs { type: rabbitmq }"
        )
        assert "DEPENDENCY_NOT_FOUND" not in codes(result)

    def test_legacy_depends_stays_a_warning(self):
        # back-compat: legacy `depends` on an undefined name must remain W001,
        # never a hard error
        result = validate('service api { image: "app" depends: [ghost] }')
        assert "W001" in [w.code for w in result.warnings]
        assert "DEPENDENCY_NOT_FOUND" not in codes(result)

    def test_cycle_two_nodes(self):
        result = validate(
            'service a { image: "x" depends_on: [b] }\n'
            'service b { image: "y" depends_on: [a] }'
        )
        assert not result.is_valid
        err = next(e for e in result.errors if e.code == "DEPENDENCY_CYCLE")
        assert "a -> b -> a" in err.message
        assert err.hint is not None

    def test_cycle_three_nodes(self):
        result = validate(
            'service a { image: "x" depends_on: [b] }\n'
            'service b { image: "y" depends_on: [c] }\n'
            'service c { image: "z" depends_on: [a] }'
        )
        err = next(e for e in result.errors if e.code == "DEPENDENCY_CYCLE")
        assert "a -> b -> c -> a" in err.message

    def test_self_cycle(self):
        result = validate('service a { image: "x" depends_on: [a] }')
        err = next(e for e in result.errors if e.code == "DEPENDENCY_CYCLE")
        assert "a -> a" in err.message

    def test_dag_has_no_cycle_error(self):
        result = validate(
            'service a { image: "x" depends_on: [b] }\n'
            'service b { image: "y" depends_on: [c] }\n'
            'service c { image: "z" }'
        )
        assert "DEPENDENCY_CYCLE" not in codes(result)

    def test_cycle_via_legacy_depends_is_detected(self):
        result = validate(
            'service a { image: "x" depends: [b] }\n'
            'service b { image: "y" depends: [a] }'
        )
        assert "DEPENDENCY_CYCLE" in codes(result)

    def test_diamond_is_not_a_cycle(self):
        result = validate(
            'service a { image: "x" depends_on: [b, c] }\n'
            'service b { image: "y" depends_on: [d] }\n'
            'service c { image: "z" depends_on: [d] }\n'
            'service d { image: "w" }'
        )
        assert "DEPENDENCY_CYCLE" not in codes(result)

    def test_cycle_reported_once(self):
        result = validate(
            'service a { image: "x" depends_on: [b] }\n'
            'service b { image: "y" depends_on: [a] }'
        )
        cycle_errors = [e for e in result.errors if e.code == "DEPENDENCY_CYCLE"]
        assert len(cycle_errors) == 1


class TestFormatter:
    def test_fmt_keeps_depends_on(self):
        src = 'service api {\n    image: "app"\n    depends_on: [db, redis]\n}\n'
        out = format_source(src)
        assert "depends_on: [db, redis]" in out

    def test_fmt_round_trip_bare_form(self):
        src = 'service api { image: "app" depends_on: db }\n'
        out = format_source(src)
        assert "depends_on: [db]" in out
        # re-parsing the formatted output yields the same field
        svc = services(parse(out))["api"]
        assert svc.depends_on == ("db",)

    def test_fmt_idempotent_with_depends_on(self):
        src = (
            "service api {\n"
            '    image: "app"\n'
            "    depends_on: [db, redis]\n"
            "}\n"
            "\n"
            "\n"
            'service db {\n    image: "pg"\n}\n'
        )
        once = format_source(src)
        twice = format_source(once)
        assert once == twice

    def test_fmt_preserves_both_kinds(self):
        src = (
            'service api { image: "app" depends: ["q"] depends_on: [db] }\n'
            'service q { image: "q" }\n'
            'service db { image: "pg" }\n'
        )
        out = format_source(src)
        assert "depends: [q]" in out
        assert "depends_on: [db]" in out


class TestExtends:
    def test_extends_inherits_depends_on(self):
        src = (
            'service base { image: "app" depends_on: [db] }\n'
            'service db { image: "pg" }\n'
            "service api extends base { replicas: 2 }\n"
        )
        from infra.resolver.extends import ExtendsResolver

        program = ExtendsResolver().resolve(parse(src))
        svc = services(program)["api"]
        assert svc.depends_on == ("db",)

    def test_extends_own_depends_on_wins(self):
        src = (
            'service base { image: "app" depends_on: [db] }\n'
            'service db { image: "pg" }\n'
            'service cache2 { image: "redis" }\n'
            "service api extends base { depends_on: [cache2] }\n"
        )
        from infra.resolver.extends import ExtendsResolver

        program = ExtendsResolver().resolve(parse(src))
        svc = services(program)["api"]
        assert svc.depends_on == ("cache2",)


class TestGraph:
    def test_graph_draws_depends_on_edges(self, tmp_path, capsys):
        from typer.testing import CliRunner

        from infra.cli.main import app

        f = tmp_path / "main.infra"
        f.write_text(
            'service api { image: "app" depends_on: [db, cache] }\n'
            "database db { type: postgres }\n"
            "cache cache { type: redis }\n"
        )
        result = CliRunner().invoke(app, ["graph", str(f)])
        assert result.exit_code == 0
        assert "api" in result.output
        assert "db" in result.output
        assert "cache" in result.output
        # api must point at both dependencies
        lines = [ln for ln in result.output.splitlines() if "api" in ln]
        assert any("db" in ln for ln in lines)
        assert any("cache" in ln for ln in lines)

    def test_graph_merges_legacy_and_new_edges(self, tmp_path):
        from typer.testing import CliRunner

        from infra.cli.main import app

        f = tmp_path / "main.infra"
        f.write_text(
            'service api { image: "app" depends: [q] depends_on: [db] }\n'
            'service q { image: "q" }\n'
            'service db { image: "pg" }\n'
        )
        result = CliRunner().invoke(app, ["graph", str(f)])
        assert result.exit_code == 0
        api_lines = [ln for ln in result.output.splitlines() if "api" in ln]
        assert any("q" in ln for ln in api_lines)
        assert any("db" in ln for ln in api_lines)


class TestBackwardsCompat:
    def test_files_without_depends_on_still_validate(self):
        result = validate(
            'service api { image: "app" depends: [db] }\n'
            'service db { image: "pg" }'
        )
        assert result.is_valid

    def test_extended_service_without_depends_on(self):
        src = (
            'service base { image: "app" }\n'
            "service api extends base { replicas: 3 }\n"
        )
        from infra.resolver.extends import ExtendsResolver

        svc = services(ExtendsResolver().resolve(parse(src)))["api"]
        assert svc.depends == ()
        assert svc.depends_on == ()


# --------------------------------------------------------------------------- #
# Backend code generation (commit 2)
# --------------------------------------------------------------------------- #


def compile_files(src: str, target: str) -> dict:
    from infra import compile as infra_compile

    return infra_compile(parse(src), target=target).files


def k8s_deployments(files: dict) -> dict:
    import yaml

    docs = [
        d
        for d in yaml.safe_load_all("\n".join(files.values()))
        if d and d.get("kind") == "Deployment"
    ]
    return {d["metadata"]["name"]: d for d in docs}


class TestComposeBackend:
    def test_depends_on_emitted(self):
        import yaml

        files = compile_files(
            'service api { image: "app" depends_on: [db, redis] }\n'
            'service db { image: "pg" }\n'
            'service redis { image: "redis" }',
            "compose",
        )
        data = yaml.safe_load(files["docker-compose.yml"])
        deps = data["services"]["api"]["depends_on"]
        assert deps == {
            "db": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        }

    def test_legacy_and_new_merge_dedup(self):
        import yaml

        files = compile_files(
            'service api { image: "app" depends: [q] depends_on: [db, q] }\n'
            'service db { image: "pg" }\n'
            'service q { image: "q" }',
            "compose",
        )
        data = yaml.safe_load(files["docker-compose.yml"])
        deps = data["services"]["api"]["depends_on"]
        assert set(deps) == {"q", "db"}
        assert all(v == {"condition": "service_healthy"} for v in deps.values())

    def test_database_target_maps_to_compose_service(self):
        import yaml

        files = compile_files(
            'service api { image: "app" depends_on: [pg] }\n'
            "database pg { type: postgres }",
            "compose",
        )
        data = yaml.safe_load(files["docker-compose.yml"])
        assert data["services"]["api"]["depends_on"] == {
            "pg": {"condition": "service_healthy"}
        }


class TestKubernetesBackend:
    def test_init_containers_per_dependency(self):
        files = compile_files(
            'service api { image: "app" depends_on: [db, cache] }\n'
            "database db { type: postgres }\n"
            "cache cache { type: redis }",
            "kubernetes",
        )
        api = k8s_deployments(files)["api"]
        inits = api["spec"]["template"]["spec"]["initContainers"]
        assert [c["name"] for c in inits] == ["wait-for-db", "wait-for-cache"]
        assert all(c["image"] == "busybox:1.36" for c in inits)
        assert "nc -z db 5432" in inits[0]["command"][-1]
        assert "nc -z cache 6379" in inits[1]["command"][-1]

    def test_service_target_uses_service_port(self):
        files = compile_files(
            "service web { image: \"nginx\" port 8080:80 }\n"
            'service api { image: "app" depends_on: [web] }',
            "kubernetes",
        )
        api = k8s_deployments(files)["api"]
        [init] = api["spec"]["template"]["spec"]["initContainers"]
        assert "nc -z web 8080" in init["command"][-1]

    def test_service_target_without_ports_defaults_80(self):
        files = compile_files(
            'service web { image: "nginx" }\n'
            'service api { image: "app" depends_on: [web] }',
            "kubernetes",
        )
        api = k8s_deployments(files)["api"]
        [init] = api["spec"]["template"]["spec"]["initContainers"]
        assert "nc -z web 80" in init["command"][-1]

    def test_queue_target_port(self):
        files = compile_files(
            'service api { image: "app" depends_on: [jobs] }\n'
            "queue jobs { type: rabbitmq }",
            "kubernetes",
        )
        api = k8s_deployments(files)["api"]
        [init] = api["spec"]["template"]["spec"]["initContainers"]
        assert "nc -z jobs 5672" in init["command"][-1]

    def test_no_dependencies_no_init_containers(self):
        # back-compat: deployments without depends_on are unchanged
        files = compile_files('service api { image: "app" }', "kubernetes")
        api = k8s_deployments(files)["api"]
        assert "initContainers" not in api["spec"]["template"]["spec"]

    def test_legacy_depends_also_generate_init_containers(self):
        # the merged edge set covers legacy `depends` too
        files = compile_files(
            'service db { image: "pg" port 5433:5432 }\n'
            'service api { image: "app" depends: [db] }',
            "kubernetes",
        )
        api = k8s_deployments(files)["api"]
        [init] = api["spec"]["template"]["spec"]["initContainers"]
        assert "nc -z db 5433" in init["command"][-1]

    def test_undeclared_target_skipped(self):
        # validator reports DEPENDENCY_NOT_FOUND; the backend ignores it
        files = compile_files(
            'service api { image: "app" depends_on: [ghost] }', "kubernetes"
        )
        api = k8s_deployments(files)["api"]
        assert "initContainers" not in api["spec"]["template"]["spec"]


class TestTerraformBackend:
    def test_service_deployments_with_depends_on(self):
        files = compile_files(
            'service web { image: "nginx" }\n'
            'service api { image: "app" depends_on: [web, pg] }\n'
            "database pg { type: postgres }",
            "terraform",
        )
        main = files["main.tf"]
        assert 'resource "kubernetes_deployment" "api"' in main
        assert 'resource "kubernetes_deployment" "web"' in main
        assert "kubernetes_deployment.web," in main
        assert "aws_db_instance.pg," in main
        assert "depends_on = [" in main

    def test_versions_and_providers_get_kubernetes(self):
        files = compile_files(
            'service api { image: "app" depends_on: [pg] }\n'
            "database pg { type: postgres }",
            "terraform",
        )
        assert "hashicorp/kubernetes" in files["versions.tf"]
        assert 'provider "kubernetes" {}' in files["providers.tf"]

    def test_no_depends_on_keeps_legacy_output(self):
        # back-compat: programs without depends_on do not emit deployments
        files = compile_files(
            'service api { image: "app" depends: [db] }\n'
            "database db { type: postgres }",
            "terraform",
        )
        assert "kubernetes_deployment" not in files["main.tf"]
        assert "kubernetes" not in files["versions.tf"]
        assert 'provider "kubernetes"' not in files["providers.tf"]

    def test_unmappable_targets_become_comments(self):
        files = compile_files(
            'service api { image: "app" depends_on: [c, ghost] }\n'
            "cache c { type: redis }",
            "terraform",
        )
        main = files["main.tf"]
        assert "# depends_on target 'c'" in main
        assert "no Terraform resource" in main
        assert "# depends_on target 'ghost' is not declared" in main

    def test_mongodb_target_maps_to_docdb(self):
        files = compile_files(
            'service api { image: "app" depends_on: [mongo] }\n'
            "database mongo { type: mongodb }",
            "terraform",
        )
        assert "aws_docdb_cluster.mongo," in files["main.tf"]


class TestHelmBackend:
    def _chart(self, src: str) -> dict:
        out = {}
        for path, content in compile_files(src, "helm").items():
            _, _, rel = path.partition("/")
            out[rel] = content
        return out

    def test_values_contain_depends_on(self):
        import yaml

        chart = self._chart(
            'service api { image: "app" depends_on: [pg, cache] }\n'
            "database pg { type: postgres }\n"
            "cache cache { type: redis }"
        )
        values = yaml.safe_load(chart["values.yaml"])
        assert values["service"]["api"]["dependsOn"] == [
            {"name": "pg", "port": 5432},
            {"name": "cache", "port": 6379},
        ]

    def test_service_dependency_port(self):
        import yaml

        chart = self._chart(
            "service web { image: \"nginx\" port 8080:80 }\n"
            'service api { image: "app" depends_on: [web] }'
        )
        values = yaml.safe_load(chart["values.yaml"])
        assert values["service"]["api"]["dependsOn"] == [
            {"name": "web", "port": 8080}
        ]

    def test_deployment_template_renders_init_containers(self):
        chart = self._chart(
            'service api { image: "app" depends_on: [pg] }\n'
            "database pg { type: postgres }"
        )
        deployment = chart["templates/deployment.yaml"]
        assert "initContainers:" in deployment
        assert "wait-for-{{ .name }}" in deployment
        assert "{{ .Release.Name }}-{{ .name }} {{ .port }}" in deployment

    def test_no_dependencies_no_depends_on_values(self):
        import yaml

        chart = self._chart('service api { image: "app" }')
        values = yaml.safe_load(chart["values.yaml"])
        assert "dependsOn" not in values["service"]["api"]


class TestTerraformProviderRefs:
    def test_gcp_database_ref(self):
        files = compile_files(
            'service api { image: "app" depends_on: [pg] }\n'
            "database pg { type: postgres }\n"
            "cluster main { provider: gcp }",
            "terraform",
        )
        assert "google_sql_database_instance.pg," in files["main.tf"]

    def test_gcp_unsupported_engine_becomes_comment(self):
        files = compile_files(
            'service api { image: "app" depends_on: [m] }\n'
            "database m { type: mongodb }\n"
            "cluster main { provider: gcp }",
            "terraform",
        )
        main = files["main.tf"]
        assert "google_sql_database_instance.m," not in main
        assert "no Terraform resource for provider 'gcp'" in main

    def test_azure_database_ref(self):
        files = compile_files(
            'service api { image: "app" depends_on: [pg] }\n'
            "database pg { type: postgres }\n"
            "cluster main { provider: azure }",
            "terraform",
        )
        assert "azurerm_postgresql_server.pg," in files["main.tf"]

    def test_azure_unsupported_engine_becomes_comment(self):
        files = compile_files(
            'service api { image: "app" depends_on: [m] }\n'
            "database m { type: mysql }\n"
            "cluster main { provider: azure }",
            "terraform",
        )
        main = files["main.tf"]
        assert "azurerm_postgresql_server.m," not in main
        assert "no Terraform resource for provider 'azure'" in main

    def test_build_only_service_uses_placeholder_image(self):
        files = compile_files(
            'service api { build { context: "." } depends_on: [pg] }\n'
            "database pg { type: postgres }",
            "terraform",
        )
        assert 'image = "built-from-dockerfile"' in files["main.tf"]

    def test_imageless_buildless_service_uses_unknown_image(self):
        # validator raises E010, but the backend must still not crash
        files = compile_files(
            'service api { depends_on: [pg] }\ndatabase pg { type: postgres }',
            "terraform",
        )
        assert 'image = "unknown"' in files["main.tf"]
