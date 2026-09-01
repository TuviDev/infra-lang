"""`infra schema` — JSON Schema (draft-07) export of the .infra DSL, v0.7.1."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from infra.cli.main import app
from infra.schema import SCHEMA_DRAFT, SCHEMA_ID, build_schema

runner = CliRunner()

#: Top-level blocks the DSL grammar defines (grammar.lark `definition` rule).
_EXPECTED_BLOCKS = [
    "services",
    "databases",
    "caches",
    "queues",
    "storages",
    "networks",
    "network_policies",
    "secrets",
    "secret_stores",
    "custom_resources",
    "configs",
    "pipelines",
    "environments",
    "clusters",
]

_EXPECTED_DEFINITIONS = [
    "service",
    "database",
    "cache",
    "queue",
    "storage",
    "network",
    "network_policy",
    "secret",
    "secret_store",
    "environment",
    "cluster",
    "pipeline",
    "custom_resource",
    "config",
    "import",
    "variable",
]


class TestBuildSchema:
    def test_schema_is_json_serializable(self):
        text = json.dumps(build_schema())
        assert json.loads(text)["$schema"] == SCHEMA_DRAFT

    def test_draft07_marker_and_id(self):
        schema = build_schema()
        assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
        assert schema["$id"] == SCHEMA_ID
        assert schema["type"] == "object"
        assert schema["title"] == "Infra Language document"

    def test_all_block_collections_present(self):
        properties = build_schema()["properties"]
        for collection in _EXPECTED_BLOCKS:
            assert collection in properties, f"missing collection: {collection}"
            assert properties[collection]["type"] == "array"

    def test_all_key_definitions_present(self):
        definitions = build_schema()["definitions"]
        for key in _EXPECTED_DEFINITIONS:
            assert key in definitions, f"missing definition: {key}"

    def test_service_definition_documents_core_fields(self):
        service = build_schema()["definitions"]["service"]
        props = service["properties"]
        for field in ("name", "image", "replicas", "ports", "env", "depends_on"):
            assert field in props, f"missing service property: {field}"
        assert service["required"] == ["name"]

    def test_type_enums_match_ast(self):
        defs = build_schema()["definitions"]
        assert defs["database"]["properties"]["type"]["enum"] == [
            "postgres",
            "mysql",
            "mongodb",
            "redis",
            "mariadb",
            "sqlite",
        ]
        assert defs["cache"]["properties"]["type"]["enum"] == [
            "redis",
            "valkey",
            "memcached",
        ]
        assert defs["queue"]["properties"]["type"]["enum"] == [
            "rabbitmq",
            "kafka",
            "nats",
        ]
        assert defs["storage"]["properties"]["type"]["enum"] == [
            "s3",
            "gcs",
            "azure_blob",
            "minio",
            "pvc",
            "efs",
        ]

    def test_refs_resolve_within_schema(self):
        # every local $ref must point at an existing definition
        refs = set()

        def _walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "$ref":
                        refs.add(value)
                    else:
                        _walk(value)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        schema = build_schema()
        _walk(schema)
        for ref in refs:
            assert ref.startswith("#/definitions/")
            target = ref.removeprefix("#/definitions/")
            assert target in schema["definitions"], f"dangling $ref: {ref}"


class TestSchemaCli:
    def test_stdout_is_valid_json(self):
        r = runner.invoke(app, ["schema"])
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.output)
        assert parsed["$schema"] == SCHEMA_DRAFT
        assert "service" in parsed["definitions"]

    def test_output_option_writes_file(self, tmp_path: Path):
        out = tmp_path / "infra-schema.json"
        r = runner.invoke(app, ["schema", "-o", str(out)])
        assert r.exit_code == 0, r.output
        assert "[OK]" in r.output
        parsed = json.loads(out.read_text(encoding="utf-8"))
        assert parsed["$id"] == SCHEMA_ID
        for collection in _EXPECTED_BLOCKS:
            assert collection in parsed["properties"]

    def test_output_round_trips_stdout_content(self, tmp_path: Path):
        out = tmp_path / "schema.json"
        r_file = runner.invoke(app, ["schema", "--output", str(out)])
        r_stdout = runner.invoke(app, ["schema"])
        assert r_file.exit_code == 0 and r_stdout.exit_code == 0
        file_json = json.loads(out.read_text(encoding="utf-8"))
        stdout_json = json.loads(r_stdout.output)
        assert file_json == stdout_json

    def test_help_lists_schema_command(self):
        r = runner.invoke(app, ["--help"])
        assert r.exit_code == 0
        assert "schema" in r.output
