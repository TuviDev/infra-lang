"""Tests for v0.5.0 secret stores (``secret_store`` + ExternalSecrets).

Covers grammar/AST parsing (quoted names, reserved-word keys like ``path``,
bare provider identifiers), the ``store:`` binding inside ``secret`` blocks,
formatter round-trips, validator rules (``STORE_NOT_FOUND``,
``INVALID_STORE_PROVIDER``) and code generation in all backends:
Kubernetes SecretStore/ExternalSecret CRDs, Compose ``external: true``
secrets and Terraform per-provider secret-manager resources.
"""

from __future__ import annotations

import pytest

from infra.analyzer.validator import SemanticValidator
from infra.cli.printer import format_source
from infra.parser import Parser
from infra.parser import ast_nodes as n

P = Parser()

VAULT = (
    'secret_store "vault_store" {\n'
    '    provider: "vault"\n'
    '    address: "https://vault.internal:8200"\n'
    '    path: "secret/data/app"\n'
    "}\n"
)


def parse(src: str) -> n.Program:
    return P.parse(src, filename="s.infra")


def validate(src: str):
    return SemanticValidator().validate(parse(src))


def compile_files(src: str, target: str) -> dict:
    from infra import compile as infra_compile

    return infra_compile(parse(src), target=target).files


def k8s_docs(files: dict) -> list:
    import yaml

    return [
        d
        for d in yaml.safe_load_all("\n".join(files.values()))
        if d is not None
    ]


class TestParsing:
    def test_vault_store_full_form(self):
        prog = parse(VAULT)
        [st] = [s for s in prog.statements if isinstance(s, n.SecretStoreDef)]
        assert st.name == "vault_store"
        assert st.provider == "vault"
        assert st.address == "https://vault.internal:8200"
        assert st.path == "secret/data/app"

    def test_aws_store_bare_provider_single_line(self):
        prog = parse(
            'secret_store "aws_store" { provider: aws, region: "eu-central-1" }'
        )
        [st] = [s for s in prog.statements if isinstance(s, n.SecretStoreDef)]
        assert st.provider == "aws"
        assert st.region == "eu-central-1"

    def test_gcp_project_property(self):
        prog = parse('secret_store "g" { provider: "gcp", project: "my-proj" }')
        [st] = [s for s in prog.statements if isinstance(s, n.SecretStoreDef)]
        assert st.project == "my-proj"

    def test_reserved_word_keys(self):
        # path/provider/region/namespace are grammar keywords elsewhere —
        # they must still parse as secret_store keys
        prog = parse(
            'secret_store "k" { provider: "kubernetes", '
            'namespace: "secrets", region: "eu", path: "data" }'
        )
        [st] = [s for s in prog.statements if isinstance(s, n.SecretStoreDef)]
        assert st.namespace == "secrets"
        assert st.path == "data"
        assert st.region == "eu"

    def test_unknown_keys_preserved_in_extra(self):
        prog = parse('secret_store "v" { provider: "vault", auth_type: "token" }')
        [st] = [s for s in prog.statements if isinstance(s, n.SecretStoreDef)]
        assert dict(st.extra) == {"auth_type": n.Literal(value="token")}

    def test_secret_store_binding(self):
        prog = parse(
            VAULT + 'secret api { store: "vault_store" password: from env "DB_PASS" }'
        )
        [sec] = [s for s in prog.statements if isinstance(s, n.SecretDef)]
        assert sec.store == "vault_store"
        assert [e.name for e in sec.entries] == ["password"]

    def test_legacy_secret_without_store(self):
        prog = parse('secret api { password: from env "P" }')
        [sec] = [s for s in prog.statements if isinstance(s, n.SecretDef)]
        assert sec.store is None

    def test_store_binding_with_literal_entry(self):
        prog = parse(VAULT + 'secret api { store: "vault_store" t: "x" }')
        [sec] = [s for s in prog.statements if isinstance(s, n.SecretDef)]
        assert sec.store == "vault_store"
        assert sec.entries[0].value == "x"


class TestFormatter:
    def test_fmt_round_trip_store(self):
        src = VAULT
        out = format_source(src)
        assert 'secret_store "vault_store" {' in out
        assert 'provider: "vault"' in out
        assert 'path: "secret/data/app"' in out
        # idempotent
        assert format_source(out) == out

    def test_fmt_keeps_store_binding(self):
        src = (
            VAULT + 'secret api {\n    store: "vault_store"\n'
            '    password: from env "DB_PASS"\n}\n'
        )
        out = format_source(src)
        assert 'store: "vault_store"' in out
        assert format_source(out) == out

    def test_fmt_keeps_unknown_keys(self):
        src = 'secret_store "v" {\n    provider: "vault"\n    auth_type: "token"\n}\n'
        out = format_source(src)
        assert 'auth_type: "token"' in out


class TestValidation:
    def test_store_not_found_is_error(self):
        result = validate('secret api { store: "ghost" password: from env "P" }')
        assert not result.is_valid
        [err] = [e for e in result.errors if e.code == "STORE_NOT_FOUND"]
        assert "'ghost'" in err.message
        assert err.hint == 'Declare secret_store "ghost" or fix the store reference'

    def test_forward_reference_allowed(self):
        result = validate(
            'secret api { store: "vs" password: from env "P" }\n'
            'secret_store "vs" { provider: vault }'
        )
        assert "STORE_NOT_FOUND" not in [e.code for e in result.errors]

    def test_invalid_provider_is_error(self):
        result = validate('secret_store "x" { provider: azure }')
        [err] = [e for e in result.errors if e.code == "INVALID_STORE_PROVIDER"]
        assert "azure" in err.message
        assert "vault" in (err.hint or "")

    def test_missing_provider_is_error(self):
        result = validate('secret_store "x" { region: "eu" }')
        assert "INVALID_STORE_PROVIDER" in [e.code for e in result.errors]

    @pytest.mark.parametrize("provider", ["vault", "aws", "gcp", "kubernetes"])
    def test_valid_providers(self, provider):
        result = validate(f'secret_store "x" {{ provider: {provider} }}')
        assert "INVALID_STORE_PROVIDER" not in [e.code for e in result.errors]

    def test_legacy_secret_stays_valid(self):
        result = validate('secret api { password: from env "P" }')
        assert result.is_valid


class TestKubernetesBackend:
    def test_secret_store_manifest_vault(self):
        files = compile_files(VAULT, "kubernetes")
        docs = k8s_docs(files)
        [store] = [d for d in docs if d.get("kind") == "SecretStore"]
        assert store["apiVersion"] == "external-secrets.io/v1beta1"
        assert store["metadata"]["name"] == "vault_store"
        provider = store["spec"]["provider"]
        assert provider["vault"]["server"] == "https://vault.internal:8200"
        assert provider["vault"]["path"] == "secret/data/app"

    def test_secret_store_manifest_aws(self):
        files = compile_files(
            'secret_store "aws_store" { provider: aws, region: "eu-central-1" }',
            "kubernetes",
        )
        [store] = [d for d in k8s_docs(files) if d.get("kind") == "SecretStore"]
        assert store["spec"]["provider"]["aws"] == {
            "service": "SecretsManager",
            "region": "eu-central-1",
        }

    def test_secret_store_manifest_gcp(self):
        files = compile_files(
            'secret_store "g" { provider: "gcp", project: "my-proj" }',
            "kubernetes",
        )
        [store] = [d for d in k8s_docs(files) if d.get("kind") == "SecretStore"]
        assert store["spec"]["provider"]["gcpsm"]["projectID"] == "my-proj"

    def test_secret_store_manifest_kubernetes(self):
        files = compile_files(
            'secret_store "k" { provider: "kubernetes", namespace: "shared" }',
            "kubernetes",
        )
        [store] = [d for d in k8s_docs(files) if d.get("kind") == "SecretStore"]
        kube = store["spec"]["provider"]["kubernetes"]
        assert kube["remoteNamespace"] == "shared"

    def test_external_secret_uses_store_path(self):
        files = compile_files(
            VAULT + 'secret api { store: "vault_store" password: from env "P" }',
            "kubernetes",
        )
        [es] = [d for d in k8s_docs(files) if d.get("kind") == "ExternalSecret"]
        assert es["apiVersion"] == "external-secrets.io/v1beta1"
        spec = es["spec"]
        assert spec["secretStoreRef"] == {"name": "vault_store", "kind": "SecretStore"}
        assert spec["target"]["name"] == "api"
        [entry] = spec["data"]
        assert entry["secretKey"] == "password"
        assert entry["remoteRef"] == {"key": "secret/data/app", "property": "password"}

    def test_external_secret_without_store_path_uses_secret_name(self):
        files = compile_files(
            'secret_store "aws_store" { provider: aws }\n'
            'secret cloud { store: "aws_store" token: from env "T" }',
            "kubernetes",
        )
        [es] = [d for d in k8s_docs(files) if d.get("kind") == "ExternalSecret"]
        assert es["spec"]["data"][0]["remoteRef"] == {
            "key": "cloud",
            "property": "token",
        }

    def test_legacy_secret_still_v1_secret(self):
        files = compile_files('secret api { password: from env "P" }', "kubernetes")
        docs = k8s_docs(files)
        assert any(d.get("kind") == "Secret" for d in docs)
        assert not any(d.get("kind") == "ExternalSecret" for d in docs)
        assert not any(d.get("kind") == "SecretStore" for d in docs)


class TestComposeBackend:
    def test_store_backed_secret_is_external(self):
        import yaml

        files = compile_files(
            VAULT + 'secret api { store: "vault_store" password: from env "P" }',
            "compose",
        )
        data = yaml.safe_load(files["docker-compose.yml"])
        assert data["secrets"]["api"] == {"external": True}
        # no local file mount, no literal value anywhere
        assert "./api.txt" not in files["docker-compose.yml"]

    def test_legacy_secret_keeps_file_mount(self):
        import yaml

        files = compile_files('secret legacy { raw: "abc" }', "compose")
        data = yaml.safe_load(files["docker-compose.yml"])
        assert data["secrets"]["legacy"] == {"file": "./legacy.txt"}


class TestTerraformBackend:
    def test_vault_store_secret_and_provider(self):
        files = compile_files(
            VAULT + 'secret api { store: "vault_store" password: from env "P" }',
            "terraform",
        )
        main = files["main.tf"]
        assert 'resource "vault_generic_secret" "api"' in main
        assert 'path = "secret/data/app"' in main
        assert "hashicorp/vault" in files["versions.tf"]
        assert 'provider "vault"' in files["providers.tf"]
        assert "https://vault.internal:8200" in files["providers.tf"]

    def test_aws_store_secret(self):
        files = compile_files(
            'secret_store "aws_store" { provider: aws, region: "eu-central-1" }\n'
            'secret cloud { store: "aws_store" token: from env "T" }',
            "terraform",
        )
        main = files["main.tf"]
        assert 'resource "aws_secretsmanager_secret" "cloud"' in main
        assert 'resource "aws_secretsmanager_secret_version" "cloud"' in main
        assert "hashicorp/vault" not in files["versions.tf"]

    def test_gcp_store_secret(self):
        files = compile_files(
            'secret_store "g" { provider: "gcp", project: "p" }\n'
            'secret api { store: "g" token: from env "T" }',
            "terraform",
        )
        main = files["main.tf"]
        assert 'resource "google_secret_manager_secret" "api"' in main
        assert 'resource "google_secret_manager_secret_version" "api"' in main

    def test_kubernetes_store_secret(self):
        files = compile_files(
            'secret_store "k" { provider: "kubernetes", namespace: "shared" }\n'
            'secret api { store: "k" token: from env "T" }',
            "terraform",
        )
        main = files["main.tf"]
        assert 'resource "kubernetes_secret" "api"' in main
        assert 'namespace = "shared"' in main
        assert "hashicorp/kubernetes" in files["versions.tf"]

    def test_sensitive_variables_declared(self):
        files = compile_files(
            VAULT + 'secret api { store: "vault_store" password: from env "P" }',
            "terraform",
        )
        assert 'variable "api_password" { sensitive = true }' in files["variables.tf"]

    def test_legacy_secret_unchanged(self):
        files = compile_files('secret legacy { raw: "abc" }', "terraform")
        assert 'resource "aws_secretsmanager_secret" "legacy"' in files["main.tf"]
        assert "vault" not in files["versions.tf"]
