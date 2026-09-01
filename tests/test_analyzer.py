"""Semantic validator tests."""

from __future__ import annotations

from infra.analyzer.validator import SemanticValidator
from infra.parser import Parser

P = Parser()


def validate(src: str):
    program = P.parse(src, filename="v.infra")
    return SemanticValidator().validate(program)


def error_codes(src: str) -> list:
    return [e.code for e in validate(src).errors]


class TestErrorDetection:
    def test_undefined_variable(self):
        result = validate("service api { image: undefined_var }")
        assert not result.is_valid
        assert "E001" in [e.code for e in result.errors]
        assert any("undefined_var" in e.message for e in result.errors)

    def test_duplicate_global_name(self):
        result = validate(
            'service api { image: "nginx" }\nservice api { image: "redis" }'
        )
        assert not result.is_valid
        assert "E002" in [e.code for e in result.errors]

    def test_replicas_zero(self):
        result = validate('service api { image: "nginx" replicas: 0 }')
        assert not result.is_valid

    def test_replicas_negative(self):
        result = validate('service api { image: "nginx" replicas: -1 }')
        assert not result.is_valid

    def test_port_out_of_range_high(self):
        result = validate('service api { image: "nginx" port 99999 }')
        assert not result.is_valid
        assert "E012" in [e.code for e in result.errors]

    def test_port_zero(self):
        result = validate('service api { image: "nginx" port 0 }')
        assert not result.is_valid

    def test_unknown_database_type(self):
        result = validate("database db { type: oracle }")
        assert not result.is_valid
        assert "E020" in [e.code for e in result.errors]

    def test_unknown_cache_type(self):
        result = validate("cache c { type: banana }")
        assert not result.is_valid
        assert "E024" in [e.code for e in result.errors]

    def test_unknown_queue_type(self):
        result = validate("queue q { type: banana }")
        assert not result.is_valid

    def test_unknown_storage_type(self):
        result = validate("storage s { type: banana }")
        assert not result.is_valid

    def test_pipeline_cyclic_dependency(self):
        result = validate(
            'pipeline p { stages { a: { needs: ["b"] steps { s: { run: "1" } } } '
            'b: { needs: ["a"] steps { s: { run: "2" } } } } }'
        )
        assert not result.is_valid
        assert "E031" in [e.code for e in result.errors]

    def test_pipeline_unknown_stage_dependency(self):
        result = validate(
            'pipeline p { stages { build: { needs: ["nonexistent"] steps { s: { run: '
            '"x" } } } } }'
        )
        assert not result.is_valid
        assert "E030" in [e.code for e in result.errors]

    def test_database_replicas_zero(self):
        result = validate("database db { type: postgres replicas: 0 }")
        assert not result.is_valid
        assert "E021" in [e.code for e in result.errors]

    def test_const_cannot_be_redefined(self):
        result = validate("const X = 1\nconst X = 2")
        assert not result.is_valid

    def test_service_no_image_no_build(self):
        result = validate("service api { replicas: 2 }")
        assert not result.is_valid
        assert "E010" in [e.code for e in result.errors]


class TestWarnings:
    def test_unused_variable_warning(self):
        result = validate('let timeout = 30s\nservice api { image: "nginx" }')
        assert result.is_valid
        assert result.has_warnings
        assert any("timeout" in w.message for w in result.warnings)

    def test_depends_unknown_service_warning(self):
        result = validate('service api { image: "nginx" depends: ["db"] }')
        assert result.is_valid
        assert result.has_warnings
        assert any(w.code == "W001" for w in result.warnings)

    def test_empty_pipeline_warning(self):
        result = validate("pipeline ci { }")
        assert result.is_valid
        assert result.has_warnings

    def test_duplicate_import_warning(self):
        result = validate('import "./a.infra"\nimport "./a.infra"')
        assert result.is_valid
        assert result.has_warnings


class TestSuggestions:
    def test_typo_in_database_type(self):
        result = validate("database db { type: postgress }")
        assert not result.is_valid
        error = next(e for e in result.errors if e.code == "E020")
        assert error.hint is not None
        assert "postgres" in error.hint

    def test_typo_case_sensitive(self):
        result = validate("database db { type: Postgres }")
        assert not result.is_valid
        error = next(e for e in result.errors if e.code == "E020")
        assert error.hint is not None
        assert "postgres" in error.hint.lower()


class TestValidCode:
    def test_valid_minimal_service(self):
        result = validate('service api { image: "nginx" }')
        assert result.is_valid
        assert not result.errors

    def test_valid_service_with_depends(self):
        result = validate(
            'service db { image: "postgres" }\n'
            'service api { image: "myapp" depends: ["db"] }'
        )
        assert result.is_valid
        assert not result.errors

    def test_valid_pipeline_with_deps(self):
        result = validate(
            "pipeline ci { stages { "
            'test: { steps { s: { run: "1" } } } '
            'build: { needs: ["test"] steps { s: { run: "2" } } } '
            'deploy: { needs: ["build"] steps { s: { run: "3" } } } } }'
        )
        assert result.is_valid

    def test_valid_all_database_types(self):
        for db_type in ["postgres", "mysql", "mariadb", "mongodb", "redis", "sqlite"]:
            result = validate(f"database db {{ type: {db_type} }}")
            assert result.is_valid, f"Type {db_type} should be valid"

    def test_valid_all_cache_types(self):
        for ct in ["redis", "valkey", "memcached"]:
            assert validate(f"cache c {{ type: {ct} }}").is_valid

    def test_valid_all_queue_types(self):
        for qt in ["rabbitmq", "kafka", "nats"]:
            assert validate(f"queue q {{ type: {qt} }}").is_valid

    def test_valid_all_storage_types(self):
        for st in ["s3", "gcs", "azure_blob", "minio", "pvc", "efs"]:
            assert validate(f"storage s {{ type: {st} }}").is_valid

    def test_error_location_is_set(self):
        result = validate("service api {\n  image: undefined_var\n}")
        error = next(e for e in result.errors if e.code == "E001")
        assert error.location is not None
        assert error.location.line > 0

    def test_error_message_not_empty(self):
        result = validate("service api { image: missing }")
        for e in result.errors:
            assert e.message


class TestExpressionTypeInference:
    """Validating these must accept the correct expression types (no errors)."""

    def test_percentage_expression_accepted(self):
        src = 'service s { image: "x:1" disruption { min_available: 50% } }'
        assert validate(src).is_valid

    def test_resource_value_expression_accepted(self):
        src = 'service s { image: "x:1" resources { requests { cpu: 100m } } }'
        assert validate(src).is_valid

    def test_duration_expression_accepted(self):
        src = 'service s { image: "x:1" health http("/") { interval: 10s } }'
        assert validate(src).is_valid

    def test_map_expression_accepted(self):
        src = 'service s { image: "x:1" labels: { tier: "web" } }'
        assert validate(src).is_valid
