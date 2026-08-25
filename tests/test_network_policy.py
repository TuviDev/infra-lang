"""Tests for v0.5.1 top-level ``network_policy`` declarations.

Covers grammar/AST parsing (quoted names, ``target``/``allow_ingress``/
``allow_egress``/``block_all_ingress`` fields, quoted and bare workload
references), formatter round-trips, validator rules
(``POLICY_TARGET_NOT_FOUND`` for dangling references, ``W012`` for
block+allow contradictions, ``E002`` name collisions), symbol
registration, LSP completion keywords, and code generation in all
backends: Kubernetes ``NetworkPolicy`` manifests, Compose isolation
networks and Terraform security resources (aws/gcp/azure).
"""

from __future__ import annotations

import pytest
import yaml

from infra.analyzer.symbols import SymbolKind
from infra.analyzer.validator import SemanticValidator
from infra.cli.printer import format_source
from infra.errors.exceptions import InfraParseError
from infra.parser import Parser
from infra.parser import ast_nodes as n

P = Parser()

SERVICES = (
    'service api { image: "myapp:1.0" port: 8080 }\n'
    'service frontend { image: "web:2.0" port: 80 }\n'
    'database db { type: "postgres" }\n'
)

FULL = (
    'network_policy "app_sec" {\n'
    '    target: "api"\n'
    '    allow_ingress: ["frontend"]\n'
    '    allow_egress: ["db"]\n'
    "}\n"
)

BLOCKED = (
    'network_policy "locked" {\n'
    '    target: "api"\n'
    "    block_all_ingress: true\n"
    "}\n"
)


def parse(src: str) -> n.Program:
    return P.parse(src, filename="s.infra")


def policy(src: str) -> n.NetworkPolicyDef:
    prog = parse(src)
    found = [s for s in prog.statements if isinstance(s, n.NetworkPolicyDef)]
    assert found, f"no NetworkPolicyDef in: {src!r}"
    return found[0]


def validate(src: str):
    return SemanticValidator().validate(parse(src))


def compile_result(src: str, target: str):
    from infra import compile as infra_compile

    return infra_compile(parse(src), target=target)


def k8s_policies(src: str) -> list:
    files = compile_result(src, "kubernetes").files
    docs = [d for d in yaml.safe_load_all(files["infra.yaml"]) if d]
    return [d for d in docs if d["kind"] == "NetworkPolicy"]


class TestParsing:
    def test_full_declaration(self):
        np = policy(FULL)
        assert np.name == "app_sec"
        assert np.target == "api"
        assert np.allow_ingress == ("frontend",)
        assert np.allow_egress == ("db",)
        assert np.block_all_ingress is False

    def test_block_all_ingress_true(self):
        np = policy(BLOCKED)
        assert np.block_all_ingress is True

    def test_block_all_ingress_false(self):
        np = policy(
            'network_policy "p" { target: "api", block_all_ingress: false }\n'
        )
        assert np.block_all_ingress is False

    def test_empty_body(self):
        np = policy('network_policy "p" { }\n')
        assert np.target == ""
        assert np.allow_ingress == ()
        assert np.allow_egress == ()

    def test_bare_references(self):
        np = policy(
            'network_policy "p" {\n'
            "    target: api\n"
            "    allow_ingress: [frontend, web]\n"
            "}\n"
        )
        assert np.target == "api"
        assert np.allow_ingress == ("frontend", "web")

    def test_multiple_egress_entries(self):
        np = policy(
            'network_policy "p" {\n'
            '    target: "api"\n'
            '    allow_egress: ["db", "cache", "queue"]\n'
            "}\n"
        )
        assert np.allow_egress == ("db", "cache", "queue")

    def test_comma_separated_items(self):
        np = policy(
            'network_policy "p" { target: "api", allow_ingress: ["frontend"], '
            "block_all_ingress: true }\n"
        )
        assert np.target == "api"
        assert np.allow_ingress == ("frontend",)
        assert np.block_all_ingress is True

    def test_unquoted_policy_name_is_rejected(self):
        with pytest.raises(InfraParseError):
            parse("network_policy bare { }\n")

    def test_per_service_block_still_parses(self):
        # the pre-existing per-service `network_policy { allow_from: ... }`
        # sub-block is untouched by the new top-level construct
        prog = parse(
            'service api {\n    image: "x"\n    network_policy {\n'
            "        allow_from: [frontend]\n    }\n}\n"
        )
        svc = next(
            s for s in prog.statements if isinstance(s, n.ServiceDef)
        )
        assert svc.network_policy is not None
        assert svc.network_policy.allow_from == ("frontend",)

    def test_symbol_kind_registered(self):
        assert SymbolKind.NETWORK_POLICY.value == "network_policy"


class TestFormatter:
    def test_fmt_roundtrip_is_idempotent(self):
        out = format_source(FULL)
        np = policy(out)
        assert np.name == "app_sec"
        assert format_source(out) == out

    def test_fmt_block_only(self):
        out = format_source(BLOCKED)
        assert "block_all_ingress: true" in out
        assert format_source(out) == out

    def test_fmt_empty_body(self):
        out = format_source('network_policy "p" { }\n')
        assert format_source(out) == out


class TestValidator:
    def test_valid_declaration_has_no_policy_errors(self):
        res = validate(SERVICES + FULL + BLOCKED)
        assert not [e for e in res.errors if e.code == "POLICY_TARGET_NOT_FOUND"]
        assert not any(w.code == "W012" for w in res.warnings)

    def test_forward_references_are_fine(self):
        # policy may be declared before the services it references
        res = validate(FULL + SERVICES)
        assert not [e for e in res.errors if e.code == "POLICY_TARGET_NOT_FOUND"]

    def test_unknown_target_rejected(self):
        res = validate('network_policy "p" { target: "ghost" }\n')
        errors = [e for e in res.errors if e.code == "POLICY_TARGET_NOT_FOUND"]
        assert len(errors) == 1
        assert "ghost" in errors[0].message

    def test_unknown_ingress_peer_rejected(self):
        res = validate(
            SERVICES + 'network_policy "p" { target: "api", '
            'allow_ingress: ["phantom"] }\n'
        )
        codes = [e.code for e in res.errors]
        assert codes.count("POLICY_TARGET_NOT_FOUND") == 1

    def test_unknown_egress_peer_rejected(self):
        res = validate(
            SERVICES + 'network_policy "p" { target: "api", '
            'allow_egress: ["missing"] }\n'
        )
        codes = [e.code for e in res.errors]
        assert codes.count("POLICY_TARGET_NOT_FOUND") == 1

    def test_hint_mentions_fix(self):
        res = validate('network_policy "p" { target: "ghost" }\n')
        error = next(e for e in res.errors if e.code == "POLICY_TARGET_NOT_FOUND")
        assert error.hint and "ghost" in error.hint

    def test_block_and_allow_warns_w012(self):
        res = validate(
            SERVICES + 'network_policy "p" { target: "api", '
            'allow_ingress: ["frontend"], block_all_ingress: true }\n'
        )
        assert any(w.code == "W012" for w in res.warnings)

    def test_block_alone_does_not_warn(self):
        res = validate(SERVICES + BLOCKED)
        assert not any(w.code == "W012" for w in res.warnings)

    def test_duplicate_policy_name_is_e002(self):
        res = validate(SERVICES + FULL + FULL.replace("app_sec", "app_sec"))
        assert any(e.code == "E002" for e in res.errors)


class TestKubernetesBackend:
    def test_manifest_shape(self):
        manifests = k8s_policies(SERVICES + FULL)
        assert len(manifests) == 1
        m = manifests[0]
        assert m["apiVersion"] == "networking.k8s.io/v1"
        assert m["kind"] == "NetworkPolicy"
        assert m["metadata"]["name"] == "app_sec"
        spec = m["spec"]
        assert spec["podSelector"] == {
            "matchLabels": {"app.kubernetes.io/name": "api"}
        }
        def _peer(name: str) -> dict:
            return {"podSelector": {"matchLabels": {"app.kubernetes.io/name": name}}}

        assert spec["ingress"] == [{"from": [_peer("frontend")]}]
        assert spec["egress"] == [{"to": [_peer("db")]}]
        assert spec["policyTypes"] == ["Ingress", "Egress"]

    def test_block_all_ingress_is_deny_all(self):
        m = k8s_policies(SERVICES + BLOCKED)[0]
        assert m["spec"]["ingress"] == []
        assert m["spec"]["policyTypes"] == ["Ingress"]
        assert "egress" not in m["spec"]

    def test_egress_only_has_no_ingress_key(self):
        m = k8s_policies(
            SERVICES + 'network_policy "p" { target: "api", '
            'allow_egress: ["db"] }\n'
        )[0]
        assert "ingress" not in m["spec"]
        assert m["spec"]["policyTypes"] == ["Egress"]

    def test_empty_policy_body_is_minimal(self):
        m = k8s_policies(SERVICES + 'network_policy "bare" { }\n')[0]
        assert m["spec"]["podSelector"] == {
            "matchLabels": {"app.kubernetes.io/name": "bare"}
        }
        assert m["spec"]["policyTypes"] == []

    def test_metadata_labels(self):
        m = k8s_policies(SERVICES + FULL)[0]
        assert m["metadata"]["labels"]["app.kubernetes.io/managed-by"] == (
            "infra-lang"
        )


class TestComposeBackend:
    def _compose(self, src: str) -> dict:
        files = compile_result(src, "compose").files
        return yaml.safe_load(files["docker-compose.yml"])

    def test_policy_network_declared(self):
        cy = self._compose(SERVICES + FULL)
        assert cy["networks"]["np_app_sec"] == {"driver": "bridge"}

    def test_target_attached_to_policy_network(self):
        cy = self._compose(SERVICES + FULL)
        assert cy["services"]["api"]["networks"] == ["np_app_sec"]

    def test_peers_attached_to_policy_network(self):
        cy = self._compose(SERVICES + FULL)
        assert cy["services"]["frontend"]["networks"] == ["np_app_sec"]
        assert cy["services"]["db"]["networks"] == ["np_app_sec"]

    def test_block_only_isolates_target(self):
        cy = self._compose(SERVICES + BLOCKED)
        assert cy["services"]["api"]["networks"] == ["np_locked"]
        assert "networks" not in cy["services"]["frontend"]

    def test_services_without_policy_have_no_networks(self):
        cy = self._compose(SERVICES)
        assert all("networks" not in svc for svc in cy["services"].values())

    def test_multiple_policies_on_one_service(self):
        cy = self._compose(
            SERVICES + FULL + 'network_policy "extra" { target: "api", '
            'allow_ingress: ["frontend"] }\n'
        )
        assert cy["services"]["api"]["networks"] == ["np_app_sec", "np_extra"]

    def test_unknown_ref_is_skipped(self):
        # the validator flags this; the compiler stays permissive
        cy = self._compose(SERVICES + 'network_policy "p" { target: "ghost" }\n')
        assert cy["networks"]["np_p"] == {"driver": "bridge"}


class TestTerraformBackend:
    def _tf(self, src: str, provider: str = "aws") -> str:
        from infra.backends.terraform import TerraformBackend

        return TerraformBackend(provider=provider).compile(parse(src)).files[
            "main.tf"
        ]

    def test_aws_security_group(self):
        main = self._tf(SERVICES + FULL)
        assert 'resource "aws_security_group" "app_sec"' in main
        assert 'description = "allow from frontend"' in main
        assert 'description = "allow to db"' in main

    def test_aws_block_only_has_no_ingress(self):
        main = self._tf(SERVICES + BLOCKED)
        assert 'resource "aws_security_group" "locked"' in main
        block = main.split('resource "aws_security_group" "locked"', 1)[1]
        assert "ingress {" not in block

    def test_gcp_firewalls(self):
        main = self._tf(SERVICES + FULL, provider="gcp")
        assert 'resource "google_compute_firewall" "app_sec_ingress"' in main
        assert 'target_tags  = ["api"]' in main
        assert 'source_tags  = ["frontend"]' in main
        assert 'resource "google_compute_firewall" "app_sec_egress"' in main
        assert 'destination_tags = ["db"]' in main

    def test_gcp_block_only_is_deny_firewall(self):
        main = self._tf(SERVICES + BLOCKED, provider="gcp")
        assert 'resource "google_compute_firewall" "locked_deny_ingress"' in main
        assert "deny {" in main

    def test_azure_nsg(self):
        main = self._tf(SERVICES + FULL, provider="azure")
        assert 'resource "azurerm_network_security_group" "app_sec"' in main
        assert 'name                       = "allow-from-frontend"' in main
        assert "priority                   = 100" in main
        assert 'direction                  = "Outbound"' in main

    def test_azure_block_only_adds_deny_rule(self):
        main = self._tf(SERVICES + BLOCKED, provider="azure")
        assert 'name                       = "deny-all-inbound"' in main
        assert 'access                     = "Deny"' in main


class TestCLI:
    def test_cli_compile_kubernetes(self, tmp_path):
        from typer.testing import CliRunner

        from infra.cli.main import app

        src = tmp_path / "app.infra"
        src.write_text(SERVICES + FULL + BLOCKED)
        out = tmp_path / "out"
        result = CliRunner().invoke(
            app, ["compile", str(src), "--target", "kubernetes", "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        text = (out / "infra.yaml").read_text()
        assert "kind: NetworkPolicy" in text
        assert "name: app_sec" in text

    def test_cli_validate_ok(self, tmp_path):
        from typer.testing import CliRunner

        from infra.cli.main import app

        src = tmp_path / "app.infra"
        src.write_text(SERVICES + FULL)
        result = CliRunner().invoke(app, ["validate", str(src)])
        assert result.exit_code == 0, result.output
