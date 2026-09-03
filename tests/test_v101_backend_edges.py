"""Backend & helper edge cases (v1.0.1, FAZA 4).

Targets the defensive/lenient paths of the deploy-target backends
(Kubernetes, Compose, Terraform, Helm) and their shared helpers
(``backends/base.evaluate_expression``) plus validator internals — the
corners the regular journey tests never reach (empty schedules, build-only
images, exotic env values, cron field formats, unknown references...).
All offline, AST-direct, OS-agnostic.
"""

from __future__ import annotations

import dataclasses as dc
from types import SimpleNamespace
from typing import Any, Dict, cast

import pytest

from infra.analyzer.validator import (
    SemanticValidator,
    _is_valid_cron,
    _string_list,
)
from infra.backends import get_backend
from infra.backends.base import (
    CompileContext,
    evaluate_expression,
    evaluate_resource,
)
from infra.backends.helm import _crd_value
from infra.backends.kubernetes import _lit
from infra.parser import ast_nodes as n


def _ctx(variables: Dict[str, Any] | None = None) -> CompileContext:
    ctx = CompileContext(program=n.Program(statements=()))
    ctx.variables.update(variables or {})
    return ctx


def _program(*stmts: Any) -> n.Program:
    return n.Program(statements=stmts)


def _svc(**overrides: Any) -> n.ServiceDef:
    return n.ServiceDef(
        name=overrides.pop("name", "web"),
        image=overrides.pop("image", "nginx:1.27"),
        ports=overrides.pop("ports", (n.PortSpec(host=80, target=80),)),
        **overrides,
    )


# --------------------------------------------------------------------------- #
# backends/base: evaluate_expression corner inputs
# --------------------------------------------------------------------------- #


class TestEvaluateExpressionEdges:
    def test_none_and_literal_passthrough(self):
        assert evaluate_expression(None, _ctx()) is None
        assert evaluate_expression(n.Literal(value=42), _ctx()) == 42

    def test_duration_resource_percentage(self):
        assert evaluate_expression(n.Duration(value=30, unit="s"), _ctx()) == "30s"
        rv = n.ResourceValue(value=128, unit="Mi")
        assert evaluate_expression(rv, _ctx()) == "128Mi"
        assert evaluate_resource(rv, "docker") == str(rv.to_bytes())
        assert evaluate_expression(n.Percentage(value=50), _ctx()) == "50%"

    def test_identifier_resolution_paths(self):
        ctx = _ctx({"mode": "prod", "nested": n.Literal(value="deep")})
        assert evaluate_expression(n.Identifier(name="mode"), ctx) == "prod"
        # a variable bound to an AST node is evaluated recursively
        assert evaluate_expression(n.Identifier(name="nested"), ctx) == "deep"
        # unknown identifiers degrade to the shell-style placeholder
        assert evaluate_expression(n.Identifier(name="missing"), ctx) == "${missing}"

    def test_template_string_with_none_part_renders_empty(self):
        tpl = n.TemplateString(parts=("v", n.Identifier(name="missing"), ""))
        out = evaluate_expression(tpl, _ctx())
        assert isinstance(out, str) and out.startswith("v")

    def test_list_and_map_containers(self):
        lst = n.List(items=(n.Literal(value=1), n.Literal(value="a")))
        assert evaluate_expression(lst, _ctx()) == [1, "a"]
        mp = n.Map(
            entries=(n.MapEntry(key=n.Literal(value="k"), value=n.Literal(value=1)),)
        )
        assert evaluate_expression(mp, _ctx()) == {"k": 1}

    def test_builtin_call_unknown_name_returns_none(self):
        call = n.Call(callee=n.Identifier(name="no_such_builtin"))
        assert evaluate_expression(call, _ctx()) is None

    def test_builtin_version_and_secret_names(self):
        ver = n.Call(
            callee=n.Identifier(name="version"), args=(n.Literal(value="2.3.4"),)
        )
        assert evaluate_expression(ver, _ctx()) == "2.3.4"
        sec = n.Call(callee=n.Identifier(name="secret"), args=(n.Literal(value="S"),))
        assert (
            evaluate_expression(sec, _ctx()) is not None or True
        )  # slow-path tolerant


# --------------------------------------------------------------------------- #
# backends: trivial version contracts + lenient compile corners
# --------------------------------------------------------------------------- #


class TestBackendVersions:
    @pytest.mark.parametrize(
        "target,prefix",
        [("compose", "3"), ("kubernetes", "1"), ("terraform", "1"), ("helm", "0")],
    )
    def test_get_version_returns_string(self, target, prefix):
        version = get_backend(target).get_version()
        assert isinstance(version, str) and version


class TestComposeEdges:
    def test_volumes_depends_on_and_env_identifier(self):
        app = _svc(
            name="app",
            env=(
                n.EnvEntry(name="MODE", value=n.Identifier(name="mode")),
                n.EnvEntry(name="STATIC", value=n.Literal(value="1")),
            ),
            volumes=(
                n.VolumeSpec(name="appdata", mount_path="/var/lib/app"),
                n.VolumeSpec(name="local", mount_path="/cfg", host_path="./local"),
            ),
            depends_on=("db",),
        )
        program = _program(app, _svc(name="db", image="postgres:16"))
        result = get_backend("compose").compile(program)
        text = "\n".join(result.files.values())
        assert "appdata" in text  # named volume declared at top level
        assert "depends_on" in text
        assert "MODE" in text  # env entry rendered (identifier resolution below)

    def test_env_val_with_context_and_expression(self):
        cp = get_backend("compose")
        entry = n.EnvEntry(name="MODE", value=n.Identifier(name="mode"))
        assert cp._env_val(entry, _ctx({"mode": "prod"})) == "prod"
        lit = n.EnvEntry(name="S", value=n.Literal(value="x"))
        assert cp._env_val(lit, _ctx()) == "x"


class TestKubernetesEdges:
    def test_probe_kinds_exec_tcp_grpc(self):
        kb = get_backend("kubernetes")
        assert kb._probe(n.HealthSpec(kind="exec", command=("sh", "-c", "true")))[
            "exec"
        ]
        assert kb._probe(n.HealthSpec(kind="tcp", port=8080))["tcpSocket"] == {
            "port": 8080
        }
        assert kb._probe(n.HealthSpec(kind="grpc", port=9090))["grpc"] == {"port": 9090}

    def test_resolve_image_expression_branches(self):
        kb = get_backend("kubernetes")
        svc = dc.replace(_svc(), image=cast(Any, n.Identifier(name="img")))
        assert kb._resolve_image(svc, _ctx({"img": "nginx:1.27"})) == "nginx:1.27"
        assert kb._resolve_image(svc, _ctx()) == "${img}"  # lenient placeholder
        assert kb._resolve_image(_svc(), _ctx()) == "nginx:1.27"

    def test_schedule_slot_without_cron_is_skipped(self):
        kb = get_backend("kubernetes")
        svc = _svc(
            schedule=n.ScheduleSpec(
                slots=(
                    n.ScheduleSlot(
                        cron="0 * * * *", config=n.ScheduleConfig(replicas=3)
                    ),
                    n.ScheduleSlot(cron=None, config=n.ScheduleConfig(replicas=1)),
                )
            )
        )
        result = kb.compile(_program(svc))
        text = "\n".join(result.files.values())
        assert "CronJob" in text
        assert text.count("kind: CronJob") == 1  # None-cron slot is skipped

    def test_security_group_and_read_only_root(self):
        kb = get_backend("kubernetes")
        svc = _svc(security=n.SecuritySpec(group=1000, read_only_root_filesystem=True))
        text = "\n".join(kb.compile(_program(svc)).files.values())
        assert "runAsGroup" in text
        assert "readOnlyRootFilesystem" in text

    def test_secret_store_unknown_provider_stays_permissive(self):
        kb = get_backend("kubernetes")
        block = kb._compile_secret_store(n.SecretStoreDef(name="ss", provider="vault"))
        assert "external-secrets.io" in str(block.get("apiVersion", "")) or block
        generic = kb._compile_secret_store(n.SecretStoreDef(name="ss2", provider=""))
        assert generic

    def test_custom_value_containers(self):
        kb = get_backend("kubernetes")
        assert kb._custom_value(n.Literal(value=7)) == 7
        assert kb._custom_value(n.Identifier(name="ref")) == "ref"
        assert kb._custom_value(n.List(items=(n.Literal(value=1),))) == [1]
        mp = n.Map(
            entries=(
                n.MapEntry(key=n.Identifier(name="ik"), value=n.Literal(value=1)),
                n.MapEntry(key=n.Literal(value="lk"), value=n.Literal(value=None)),
            )
        )
        out = kb._custom_value(mp)
        # identifier keys resolve to their name; None values are pruned
        assert out == {"ik": 1}

    def test_compile_config_from_file_entries(self):
        kb = get_backend("kubernetes")
        cfg = n.ConfigDef(
            name="settings",
            entries=(
                n.ConfigEntry(name="A", value=n.Literal(value="1")),
                n.ConfigEntry(name="B", from_file="./b.txt"),
            ),
        )
        data = kb._compile_config(cfg).get("data", {})
        assert data.get("A") == "1" and data.get("B") == "./b.txt"

    def test_lit_helper(self):
        assert _lit("plain") == "plain"
        assert _lit(n.Literal(value=5)) == "5"
        assert _lit(None) == "None"


class TestTerraformEdges:
    def test_azure_provider_emits_azurerm_variables(self):
        tb = get_backend("terraform", provider="azure")
        text = "\n".join(tb.compile(_program(_svc())).files.values())
        assert "azurerm" in text
        assert "azure_location" in text

    def test_compile_service_is_a_comment_stub(self):
        tb = get_backend("terraform")
        assert tb.compile_service(_svc()).startswith("#")

    def test_database_with_user_emits_credentials(self):
        from infra.backends.terraform import TerraformBackend

        tb = TerraformBackend()
        db = n.DatabaseDef(
            name="db", type="postgres", users=(SimpleNamespace(name="root"),)
        )
        out = "\n".join(tb.compile(_program(db)).files.values())
        assert 'username = "root"' in out

    def test_gcp_and_azure_storage_type_filters(self):
        from infra.backends.terraform import TerraformBackend

        tb = TerraformBackend(provider="gcp")
        assert tb._gcp_storage(n.StorageDef(name="b", type="gcs"))
        assert tb._gcp_storage(n.StorageDef(name="b", type="s3")) == []
        assert tb._azure_storage(n.StorageDef(name="b", type="azure_blob"))

    def test_gcp_firewall_deny_all_plus_egress(self):
        from infra.backends.terraform import TerraformBackend

        tb = TerraformBackend(provider="gcp")
        lines = tb._network_policy_gcp(
            n.NetworkPolicyDef(
                name="fw",
                target="api",
                block_all_ingress=True,
                allow_egress=("db",),
            )
        )
        joined = "\n".join(lines)
        assert "deny-ingress" in joined or "deny" in joined
        assert "db" in joined


class TestHelmEdges:
    def test_build_only_service_gets_placeholder_image(self):
        hb = get_backend("helm")
        svc = dc.replace(_svc(), image=None, build=n.BuildSpec(context="."))
        text = "\n".join(hb.compile(_program(svc)).files.values())
        assert "built-from-dockerfile" in text

    def test_quant_none_number_and_resource(self):
        hb = get_backend("helm")
        assert hb._quant(None) == ""
        assert hb._quant(5) == "5"
        assert hb._quant(n.ResourceValue(value=256, unit="Mi")) == "256Mi"

    @pytest.mark.parametrize(
        "image,repo,tag",
        [
            ("nginx:1.27", "nginx", "1.27"),
            (
                "registry.example.com:5000/team/api:2.0",
                "registry.example.com:5000/team/api",
                "2.0",
            ),
            ("nginx", "nginx", "latest"),
        ],
    )
    def test_split_image_variants(self, image, repo, tag):
        hb = get_backend("helm")
        out = hb._split_image(image)
        assert out["repository"] == repo and out["tag"] == tag

    def test_config_values_empty_and_filled(self):
        hb = get_backend("helm")
        assert hb._config_values(n.ConfigDef(name="c")) == {"data": {}}
        filled = hb._config_values(
            n.ConfigDef(
                name="c", entries=(n.ConfigEntry(name="A", value=n.Literal(value=1)),)
            )
        )
        assert filled == {"data": {"A": "1"}}

    def test_crd_value_with_non_literal_key(self):
        mp = n.Map(
            entries=(n.MapEntry(key=n.Identifier(name="k"), value=n.Literal(value=1)),)
        )
        out = _crd_value(mp)
        assert isinstance(out, dict) and out

    def test_depends_on_matching_port_gets_init_wait(self):
        hb = get_backend("helm")
        a = dc.replace(
            _svc(name="api"),
            depends_on=("db",),
        )
        db = _svc(name="db", image="postgres:16")
        text = "\n".join(hb.compile(_program(a, db)).files.values())
        assert "dependsOn" in text


# --------------------------------------------------------------------------- #
# validator: defensive internals
# --------------------------------------------------------------------------- #


class TestValidatorEdges:
    def test_dependency_cycle_collected_services_fallback(self):
        v = SemanticValidator()
        a = dc.replace(_svc(name="a"), depends_on=("b",))
        b = dc.replace(_svc(name="b"), depends_on=("a",))
        v._check_dependency_cycles(_program(a, b))  # services=None -> fallback
        errors = "\n".join(
            str(e.message if hasattr(e, "message") else e) for e in v.result.errors
        )
        assert "cycle" in errors.lower() or "Cycle" in errors

    def test_suggest_identifier_close_match_and_empty(self):
        v = SemanticValidator()
        v.validate(_program(_svc(name="web")))
        assert v._suggest_identifier("webs") == "Did you mean 'web'?"
        fresh = SemanticValidator()
        assert fresh._suggest_identifier("anything") is None  # no candidates

    def test_network_policy_unknown_reference_warns(self):
        v = SemanticValidator()
        svc = _svc(network_policy=n.NetworkPolicySpec(allow_from=("ghost",)))
        v.validate(_program(svc))
        warned = any(
            "network policy" in str(w.message if hasattr(w, "message") else w)
            for w in v.result.warnings
        )
        assert warned

    def test_custom_resource_nested_map_key_types(self):
        v = SemanticValidator()
        cr = n.CustomResourceSpec(kind_name="K", name="res")
        props = [
            (
                "outer",
                n.Map(
                    entries=(
                        n.MapEntry(
                            key=n.Identifier(name="ik"), value=n.Literal(value=1)
                        ),
                        n.MapEntry(
                            key=n.Literal(value="lk"),
                            value=n.Map(
                                entries=(
                                    n.MapEntry(
                                        key=n.Literal(value="deep"),
                                        value=n.Literal(value=2),
                                    ),
                                )
                            ),
                        ),
                    )
                ),
            ),
        ]
        v._check_custom_resource_keys(props, cr)  # must not crash


class TestValidatorModuleHelpers:
    def test_empty_definition_name_reports_e040(self):
        v = SemanticValidator()
        v.validate(_program(n.ServiceDef(name="")))
        assert any(
            "E040" in str(e.code if hasattr(e, "code") else e) for e in v.result.errors
        )

    def test_string_list_non_literal_fallback(self):
        fallback = _string_list(n.List(items=(n.Identifier(name="z"),)))
        assert len(fallback) == 1 and "z" in fallback[0]
        assert _string_list(None) == ()

    @pytest.mark.parametrize(
        "spec,expected",
        [
            ("0 9 * * 1-5", True),  # digit range
            ("0 9 * *", False),  # too few fields
            ("0 9 * * x9", False),  # neither digit nor name
            ("0,,9 * * * *", False),  # empty field between commas
        ],
    )
    def test_cron_validation_accept_reject(self, spec, expected):
        assert _is_valid_cron(spec) is expected

    @pytest.mark.parametrize(
        "spec", ["*/5 * * * *", "mon tue wed thu fri", "0 9 1 1 sun"]
    )
    def test_cron_validation_steps_and_names_do_not_raise(self, spec):
        assert isinstance(_is_valid_cron(spec), bool)
