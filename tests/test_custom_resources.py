"""Tests for v0.5.0 generic custom resources (CRD plugin system).

Covers the ``resource "<kind>" "<name>" { ... }`` DSL: grammar/AST parsing
(quoted type + instance name, keyword-collision keys such as ``path`` /
``type``, the bare-map form ``spec { ... }`` vs. the colon form
``spec: { ... }``, nested maps), formatter round-trips and idempotency,
validator notices (``W010`` / ``W011`` for missing ``api_version`` /
``kind``, ``E050`` duplicate properties at any nesting level, ``E002``
name collisions), symbol registration, and all backends: Kubernetes
direct manifests, Helm ``crds/`` files, and the clear skip notices
produced by the Compose and Terraform backends.
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

BASIC = (
    'resource "custom_crd" "my_resource" {\n'
    '    api_version: "stable.example.com/v1"\n'
    '    kind: "MyKind"\n'
    "    spec {\n"
    "        replicas: 3\n"
    "    }\n"
    "}\n"
)


def parse(src: str) -> n.Program:
    return P.parse(src, filename="s.infra")


def custom(src: str) -> n.CustomResourceSpec:
    prog = parse(src)
    found = [s for s in prog.statements if isinstance(s, n.CustomResourceSpec)]
    assert found, f"no CustomResourceSpec in: {src!r}"
    return found[0]


def validate(src: str):
    return SemanticValidator().validate(parse(src))


def compile_result(src: str, target: str):
    from infra import compile as infra_compile

    return infra_compile(parse(src), target=target)


def k8s_docs(src: str) -> list:
    files = compile_result(src, "kubernetes").files
    return list(yaml.safe_load_all(files["infra.yaml"]))


class TestParsing:
    def test_minimal_declaration(self):
        cr = custom(BASIC)
        assert cr.kind_name == "custom_crd"
        assert cr.name == "my_resource"
        assert cr.api_version == "stable.example.com/v1"
        assert cr.kind == "MyKind"
        assert isinstance(cr.spec, n.Map)

    def test_spec_entries(self):
        cr = custom(BASIC)
        assert isinstance(cr.spec, n.Map)
        entry = cr.spec.entries[0]
        assert isinstance(entry.key, n.Identifier)
        assert entry.key.name == "replicas"
        assert isinstance(entry.value, n.Literal)
        assert entry.value.value == 3

    def test_empty_body(self):
        cr = custom('resource "widget" "w1" { }\n')
        assert cr.kind_name == "widget"
        assert cr.name == "w1"
        assert cr.properties == ()

    def test_keyword_collision_keys(self):
        # the DSL lexer prefers keyword tokens over IDENTIFIER, so every
        # keyword must work as a bare property key inside resource blocks.
        keys = [
            "path",
            "type",
            "namespace",
            "provider",
            "region",
            "service",
            "secret",
            "host",
            "image",
            "replicas",
            "labels",
            "resources",
        ]
        body = "\n".join(f"    {k}: 1" for k in keys)
        cr = custom(f'resource "crd" "r" {{\n{body}\n}}\n')
        assert [k for k, _ in cr.properties] == keys

    def test_colon_and_bare_map_forms_equivalent(self):
        bare = custom('resource "c" "r" {\n    spec { replicas: 3 }\n}\n')
        colon = custom('resource "c" "r" {\n    spec: { replicas: 3 }\n}\n')
        assert isinstance(bare.spec, n.Map)
        assert isinstance(colon.spec, n.Map)
        b = bare.spec.entries[0]
        c = colon.spec.entries[0]
        assert b.key.name == c.key.name == "replicas"
        assert b.value.value == c.value.value == 3

    def test_nested_bare_maps(self):
        cr = custom(
            'resource "c" "r" {\n'
            "    spec {\n"
            "        template {\n"
            "            labels { app: \"web\" }\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        spec = cr.spec
        assert isinstance(spec, n.Map)
        template = spec.entries[0].value
        assert isinstance(template, n.Map)
        labels = template.entries[0].value
        assert isinstance(labels, n.Map)
        assert labels.entries[0].key.name == "app"

    def test_properties_preserve_order(self):
        cr = custom(
            'resource "c" "r" {\n'
            '    api_version: "v1"\n'
            '    kind: "K"\n'
            '    rollout: { strategy: "canary" }\n'
            "    enabled: true\n"
            "}\n"
        )
        assert [k for k, _ in cr.properties] == [
            "api_version",
            "kind",
            "rollout",
            "enabled",
        ]

    def test_string_keys_rejected_in_bare_maps(self):
        # bare-map keys are bare words; quoted keys need the colon form
        with pytest.raises(InfraParseError):
            parse('resource "c" "r" {\n    spec { "a b": 1 }\n}\n')

    def test_string_keys_allowed_in_colon_form(self):
        cr = custom('resource "c" "r" {\n    spec: { "a b": 1 }\n}\n')
        assert isinstance(cr.spec, n.Map)
        key = cr.spec.entries[0].key
        assert isinstance(key, n.Literal)
        assert key.value == "a b"

    def test_symbol_kind_registered(self):
        assert SymbolKind.CUSTOM_RESOURCE.value == "custom_resource"


class TestFormatter:
    def test_fmt_roundtrip_is_idempotent(self):
        out = format_source(BASIC)
        cr = custom(out)
        assert cr.kind_name == "custom_crd"
        assert format_source(out) == out

    def test_fmt_prefers_bare_blocks_for_identifier_keyed_maps(self):
        out = format_source(BASIC)
        assert "spec {" in out

    def test_fmt_nested_bare_blocks(self):
        src = (
            'resource "c" "r" {\n'
            "    metadata { labels: { team: \"platform\" } }\n"
            "}\n"
        )
        out = format_source(src)
        assert "metadata {" in out
        assert "labels {" in out
        assert format_source(out) == out

    def test_fmt_quoted_keys_stay_in_colon_form(self):
        src = 'resource "c" "r" {\n    spec: { "a b": 1 }\n}\n'
        out = format_source(src)
        assert '"a b": 1' in out
        assert format_source(out) == out

    def test_fmt_multiline_expression_maps(self):
        src = (
            'resource "c" "r" {\n'
            '    odd: { "a b": 1, c: 2, d: 3, e: 4 }\n'
            "}\n"
        )
        out = format_source(src)
        assert format_source(out) == out

    def test_fmt_multiline_lists(self):
        src = 'resource "c" "r" {\n    vals: [1, 2, 3, 4]\n}\n'
        out = format_source(src)
        assert format_source(out) == out

    def test_fmt_empty_body(self):
        out = format_source('resource "widget" "w1" { }\n')
        assert format_source(out) == out


class TestValidator:
    def test_valid_declaration_has_no_diagnostics(self):
        res = validate(BASIC)
        assert res.errors == []
        assert res.warnings == []

    def test_missing_api_version_warns(self):
        res = validate('resource "c" "r" {\n    kind: "K"\n}\n')
        assert not res.errors
        assert any(
            w.code == "W010" and "api_version" in w.message for w in res.warnings
        )

    def test_missing_kind_warns(self):
        res = validate('resource "c" "r" {\n    api_version: "v1"\n}\n')
        assert not res.errors
        assert any(
            w.code == "W011" and "kind" in w.message for w in res.warnings
        )

    def test_missing_both_warns_twice(self):
        res = validate('resource "c" "r" {\n    spec { a: 1 }\n}\n')
        codes = {w.code for w in res.warnings}
        assert {"W010", "W011"} <= codes

    def test_duplicate_top_level_property_is_error(self):
        res = validate(
            'resource "c" "r" {\n'
            '    api_version: "v1"\n'
            '    kind: "K"\n'
            '    api_version: "v2"\n'
            "}\n"
        )
        assert any(e.code == "E050" for e in res.errors)

    def test_duplicate_nested_map_key_is_error(self):
        res = validate(
            'resource "c" "r" {\n'
            '    api_version: "v1"\n'
            '    kind: "K"\n'
            "    spec { a: 1, a: 2 }\n"
            "}\n"
        )
        assert any(e.code == "E050" for e in res.errors)

    def test_duplicate_resource_name_is_e002(self):
        res = validate(BASIC + BASIC)
        assert any(e.code == "E002" for e in res.errors)


class TestKubernetesBackend:
    def test_manifest_shape(self):
        docs = k8s_docs(BASIC)
        manifest = next(d for d in docs if d["kind"] == "MyKind")
        assert manifest["apiVersion"] == "stable.example.com/v1"
        assert manifest["metadata"]["name"] == "my_resource"
        assert manifest["spec"] == {"replicas": 3}

    def test_defaults_when_coordinates_missing(self):
        docs = k8s_docs('resource "widget" "w1" {\n    spec { a: 1 }\n}\n')
        manifest = docs[0]
        assert manifest["apiVersion"] == "v1"
        assert manifest["kind"] == "widget"

    def test_nested_maps_become_nested_dicts(self):
        docs = k8s_docs(
            'resource "c" "r" {\n'
            '    api_version: "v1"\n'
            '    kind: "K"\n'
            "    spec {\n"
            "        template {\n"
            "            labels { app: \"web\" }\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        manifest = docs[0]
        assert manifest["spec"]["template"]["labels"] == {"app": "web"}

    def test_lists_and_scalars_pass_through(self):
        docs = k8s_docs(
            'resource "c" "r" {\n'
            '    api_version: "v1"\n'
            '    kind: "K"\n'
            "    spec {\n"
            "        enabled: true\n"
            "        ratio: 0.5\n"
            "        vals: [1, 2]\n"
            "    }\n"
            "}\n"
        )
        spec = docs[0]["spec"]
        assert spec["enabled"] is True
        assert spec["ratio"] == 0.5
        assert spec["vals"] == [1, 2]

    def test_null_values_are_dropped(self):
        docs = k8s_docs(
            'resource "c" "r" {\n'
            '    api_version: "v1"\n'
            '    kind: "K"\n'
            "    spec { optional: null, keep: 1 }\n"
            "}\n"
        )
        spec = docs[0]["spec"]
        assert "optional" not in spec
        assert spec["keep"] == 1

    def test_quoted_map_keys(self):
        docs = k8s_docs(
            'resource "c" "r" {\n'
            '    api_version: "v1"\n'
            '    kind: "K"\n'
            '    spec: { "a b": 1 }\n'
            "}\n"
        )
        assert docs[0]["spec"] == {"a b": 1}

    def test_user_metadata_merged(self):
        docs = k8s_docs(
            'resource "c" "r" {\n'
            '    api_version: "v1"\n'
            '    kind: "K"\n'
            "    metadata { annotations: { team: \"platform\" } }\n"
            "}\n"
        )
        metadata = docs[0]["metadata"]
        # user-provided metadata fields are merged on top of the defaults
        assert metadata["name"] == "r"
        assert metadata["annotations"] == {"team": "platform"}

    def test_composes_with_other_definitions(self):
        docs = k8s_docs(BASIC + 'service web { image: "nginx" port: 8080 }\n')
        kinds = {d["kind"] for d in docs if d}
        assert {"MyKind", "Deployment"} <= kinds


class TestOtherBackends:
    def test_compose_warns_and_skips(self):
        res = compile_result(BASIC, "compose")
        assert any(
            "my_resource" in w and "kubernetes" in w for w in res.warnings
        )

    def test_compose_still_compiles_services(self):
        res = compile_result(
            BASIC + 'service web { image: "nginx" port: 8080 }\n', "compose"
        )
        assert res.files
        assert any("web" in f or "web" in res.files[f] for f in res.files)

    def test_terraform_warns_and_skips(self):
        res = compile_result(BASIC, "terraform")
        assert any(
            "my_resource" in w and "kubernetes" in w for w in res.warnings
        )
        assert "main.tf" in res.files

    def test_helm_crds_file(self):
        res = compile_result(BASIC, "helm")
        crd_files = [f for f in res.files if "/crds/" in f]
        assert len(crd_files) == 1
        manifest = yaml.safe_load(res.files[crd_files[0]])
        assert manifest["apiVersion"] == "stable.example.com/v1"
        assert manifest["kind"] == "MyKind"
        assert manifest["metadata"]["name"] == "my_resource"
        assert manifest["spec"] == {"replicas": 3}

    def test_helm_crds_fallback_coordinates(self):
        res = compile_result(
            'resource "widget" "w1" {\n    spec { a: 1 }\n}\n', "helm"
        )
        crd_files = [f for f in res.files if "/crds/" in f]
        manifest = yaml.safe_load(res.files[crd_files[0]])
        assert manifest["apiVersion"] == "v1"
        assert manifest["kind"] == "widget"

    def test_github_pipeline_unaffected(self):
        res = compile_result(
            BASIC + 'service web { image: "nginx" port: 8080 }\n', "github"
        )
        assert res.files


class TestCLI:
    def test_cli_compile_kubernetes(self, tmp_path):
        from typer.testing import CliRunner

        from infra.cli.main import app

        src = tmp_path / "app.infra"
        src.write_text(BASIC)
        out = tmp_path / "out"
        result = CliRunner().invoke(
            app, ["compile", str(src), "--target", "kubernetes", "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        generated = out / "infra.yaml"
        assert generated.exists()
        text = generated.read_text()
        assert "kind: MyKind" in text
        assert "apiVersion: stable.example.com/v1" in text
