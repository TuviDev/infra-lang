"""Grammar-level parsing tests.

These tests drive Lark directly (via Parser.parse) to verify that each Infra
construct produces a valid parse tree — they exercise the grammar, not the
transformer. Each group of structures is its own test class.
"""

from __future__ import annotations

import pytest

from infra.errors.exceptions import InfraParseError
from infra.parser import Parser

P = Parser()


def parses(src: str, name: str = "test.infra") -> None:
    """Assert that *src* parses without raising."""
    P.parse(src, filename=name)


def fails(src: str) -> InfraParseError:
    """Parse *src*, expecting an InfraParseError; return it."""
    with pytest.raises(InfraParseError) as excinfo:
        P.parse(src, filename="bad.infra")
    return excinfo.value


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class TestServiceParsing:
    def test_minimal_service(self):
        parses('service foo { image: "nginx" }')

    def test_service_with_plain_port(self):
        parses('service a { image: "x" port 8080 }')

    def test_service_with_colon_port(self):
        parses('service a { image: "x" port: 8080 }')

    def test_service_with_port_object(self):
        parses('service a { image: "x" port { target: 80 host: 8080 } }')

    def test_service_with_env_block(self):
        parses('service a { image: "x" env { MODE: "prod" } }')

    def test_service_with_env_from_secret(self):
        parses('service a { image: "x" env { DB: from secret "db".url } }')

    def test_service_with_resources(self):
        parses('service a { image: "x" resources { cpu: 100m memory: 128Mi } }')

    def test_service_with_health_shorthand(self):
        parses('service a { image: "x" health http("/health") }')

    def test_service_with_health_block(self):
        parses(
            'service a { image: "x" health http("/health") { interval: 10s timeout: 5s '
            '} }'
        )

    def test_service_with_volumes_list(self):
        parses(
            'service a { image: "x" volumes [ { name: "data" mountPath: "/data" } ] }'
        )

    def test_service_with_volumes_block(self):
        parses('service a { image: "x" volumes { data: { mountPath: "/data" } } }')

    def test_service_with_strategy_shorthand(self):
        parses('service a { image: "x" strategy: rolling }')

    def test_service_with_strategy_block(self):
        parses('service a { image: "x" strategy { type: "canary" } }')

    def test_service_with_security_block(self):
        parses(
            'service a { image: "x" security { user: 1000 readOnlyRootFilesystem: true '
            '} }'
        )

    def test_service_with_lifecycle(self):
        parses('service a { image: "x" lifecycle { preStop { exec: ["sleep 30"] } } }')

    def test_service_with_decorator(self):
        parses('@prod\nservice a { image: "x" }')

    def test_service_with_multiple_decorators(self):
        parses('@prod\n@regional(eu)\nservice a { image: "x" }')

    def test_service_with_depends(self):
        parses('service a { image: "x" depends: ["db", "cache"] }')

    def test_service_with_ingress(self):
        parses('service a { image: "x" port 80 ingress { host: "a.example.com" } }')

    def test_service_with_ingress_rate_limit(self):
        parses(
            'service a { image: "x" port 80 ingress { rate_limit { rps: 100 burst: 50 '
            '} '
            '} }'
        )

    def test_service_with_ingress_cors(self):
        parses('service a { image: "x" port 80 ingress { cors { origins: ["*"] } } }')

    def test_service_with_probes(self):
        parses(
            'service a { image: "x" probes { liveness http("/live") readiness '
            'http("/ready") } }'
        )

    def test_service_with_labels_annotations(self):
        parses(
            'service a { image: "x" labels: { app: "api" } annotations: { note: "hi" } '
            '}'
        )

    def test_service_with_build(self):
        parses('service a { build { context: "." dockerfile: "Dockerfile" } }')


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


class TestDatabaseParsing:
    def test_minimal_database(self):
        parses("database db { type: postgres }")

    def test_database_with_version_storage(self):
        parses('database db { type: postgres version: "15" size: 10Gi }')

    def test_database_with_replicas_ha(self):
        parses("database db { type: postgres replicas: 2 ha: true }")

    def test_database_with_backup(self):
        parses(
            'database db { type: postgres backup { enabled: true schedule: "0 2 * * *" '
            '} }'
        )

    def test_database_with_users(self):
        parses('database db { type: postgres users { admin: "pw" reader: "rw" } }')

    @pytest.mark.parametrize(
        "db_type", ["postgres", "mysql", "mariadb", "mongodb", "redis", "sqlite"]
    )
    def test_database_types(self, db_type):
        parses(f"database db {{ type: {db_type} }}")


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


class TestCacheParsing:
    def test_minimal_cache(self):
        parses("cache c { type: redis }")

    def test_cache_with_memory_replicas(self):
        parses("cache c { type: redis maxmemory: 256Mi replicas: 3 }")

    def test_cache_with_persistence(self):
        parses("cache c { type: redis persistence: true }")

    @pytest.mark.parametrize("cache_type", ["redis", "memcached", "valkey"])
    def test_cache_types(self, cache_type):
        parses(f"cache c {{ type: {cache_type} }}")


# --------------------------------------------------------------------------- #
# Queue
# --------------------------------------------------------------------------- #


class TestQueueParsing:
    def test_minimal_queue(self):
        parses("queue q { type: rabbitmq }")

    def test_queue_with_topics(self):
        parses(
            "queue q { type: kafka topics { orders: { partitions: 3 replication: 2 } } "
            "}"
        )

    def test_queue_with_users(self):
        parses('queue q { type: rabbitmq users { app: "pw" } }')

    @pytest.mark.parametrize("queue_type", ["rabbitmq", "kafka", "nats"])
    def test_queue_types(self, queue_type):
        parses(f"queue q {{ type: {queue_type} }}")


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


class TestStorageParsing:
    def test_minimal_storage(self):
        parses("storage s { type: s3 }")

    def test_storage_with_bucket_region(self):
        parses('storage s { type: s3 bucket: "my-bucket" region: "eu-west-1" }')

    def test_storage_with_lifecycle(self):
        parses("storage s { type: s3 lifecycle { expiration: 30d retention: 7d } }")

    @pytest.mark.parametrize(
        "storage_type", ["s3", "gcs", "azure_blob", "minio", "pvc", "efs"]
    )
    def test_storage_types(self, storage_type):
        parses(f"storage s {{ type: {storage_type} }}")


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #


class TestNetworkParsing:
    def test_minimal_network(self):
        parses("network n { }")

    def test_network_with_cidr_subnets(self):
        parses(
            'network n { cidr: "10.0.0.0/16" subnets { a: { cidr: "10.0.1.0/24" } } }'
        )

    def test_network_with_policies(self):
        parses(
            'network n { policy { allow_internal: { from: "10.0.0.0/16" to: "*" ports: '
            '[80, 443] } } }'
        )


# --------------------------------------------------------------------------- #
# Secret / Config
# --------------------------------------------------------------------------- #


class TestSecretParsing:
    def test_secret_with_value(self):
        parses('secret s { password: "plain" }')

    def test_secret_from_env(self):
        parses('secret s { key: from env "MY_KEY" }')

    def test_secret_from_file(self):
        parses('secret s { key: from file "/etc/key" }')

    def test_secret_from_vault(self):
        parses('secret s { key: from vault "secret/data/key" }')

    def test_secret_from_aws(self):
        parses('secret s { key: from aws "arn:aws:secretsmanager:x" }')

    def test_secret_from_gcp(self):
        parses('secret s { key: from gcp "projects/x/secrets/y" }')

    def test_secret_multiple_entries(self):
        parses('secret s { a: "1" b: from vault "x" c: from env "Y" }')


class TestConfigParsing:
    def test_config_with_value(self):
        parses('config c { log_level: "info" }')

    def test_config_from_file(self):
        parses('config c { file: "/etc/config" }')

    def test_config_multiple_entries(self):
        parses('config c { a: 1 b: true c: "x" }')


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


class TestPipelineParsing:
    def test_minimal_pipeline(self):
        parses(
            'pipeline p { stages { test: { image: "ubuntu" steps { s: { run: "x" } } } '
            '} }'
        )

    def test_pipeline_trigger_branches(self):
        parses(
            'pipeline p { trigger { branches: ["main"] } stages { t: { steps { s: { '
            'run: "x" } } } } }'
        )

    def test_pipeline_trigger_schedule(self):
        parses(
            'pipeline p { trigger { schedule: "0 0 * * *" } stages { t: { steps { s: { '
            'run: "x" } } } } }'
        )

    def test_pipeline_trigger_manual(self):
        parses(
            'pipeline p { trigger { manual: true } stages { t: { steps { s: { run: "x" '
            '} } } } }'
        )

    def test_pipeline_stage_run_and_uses(self):
        parses(
            'pipeline p { stages { t: { steps { a: { run: "echo hi" } b: { uses: '
            '"checkout" } } } } }'
        )

    def test_pipeline_stage_needs(self):
        parses(
            'pipeline p { stages { a: { steps { s: { run: "x" } } } b: { needs: ["a"] '
            'steps { s: { run: "y" } } } } }'
        )

    def test_pipeline_stage_matrix(self):
        parses(
            'pipeline p { stages { t: { matrix { python: ["3.10", "3.11"] } steps { s: '
            '{ run: "pytest" } } } } }'
        )

    def test_pipeline_stage_parallel(self):
        parses(
            'pipeline p { stages { a: { parallel { x: { steps { s: { run: "1" } } } y: '
            '{ steps { s: { run: "2" } } } } } } }'
        )

    def test_pipeline_artifacts(self):
        parses(
            'pipeline p { artifacts { upload: ["dist/"] } stages { t: { steps { s: { '
            'run: "x" } } } } }'
        )

    def test_pipeline_cache(self):
        parses(
            'pipeline p { cache { path: "~/.cache" key: "${{ runner.os }}" } stages { '
            't: { steps { s: { run: "x" } } } } }'
        )

    def test_pipeline_concurrency(self):
        parses(
            'pipeline p { concurrency { group: "deploy" cancelInProgress: true } '
            'stages '
            '{ t: { steps { s: { run: "x" } } } } }'
        )

    def test_pipeline_step_shorthand(self):
        parses('pipeline p { stages { t: { steps { hello: "echo hello" } } } }')


# --------------------------------------------------------------------------- #
# Environment / Cluster
# --------------------------------------------------------------------------- #


class TestEnvironmentParsing:
    def test_minimal_environment(self):
        parses("environment dev { }")

    def test_environment_with_namespace(self):
        parses('environment dev { namespace: "myapp-dev" }')

    def test_environment_with_labels(self):
        parses('environment dev { provider: aws labels: { tier: "app" } }')


class TestClusterParsing:
    def test_minimal_cluster(self):
        parses("cluster c { }")

    def test_cluster_with_provider(self):
        parses("cluster c { provider: aws }")

    def test_cluster_with_single_node_pool(self):
        parses(
            'cluster c { provider: aws nodes { w: { machine type: "t3.medium" min: 1 '
            'max: 5 } } }'
        )

    def test_cluster_with_multiple_node_pools(self):
        parses(
            "cluster c { provider: aws nodes { w1: { min: 1 max: 2 } w2: { min: 1 max: "
            "3 } } }"
        )

    def test_cluster_with_networking(self):
        parses('cluster c { networking { cidr: "10.0.0.0/16" } }')

    def test_cluster_with_iam(self):
        parses(
            'cluster c { iam { serviceAccount { name: "app-sa" } role { actions: '
            '["eks:DescribeCluster"] } } }'
        )


# --------------------------------------------------------------------------- #
# Expressions
# --------------------------------------------------------------------------- #


class TestExpressionParsing:
    @pytest.mark.parametrize("src", ["42", "3.14", "0xFF", "0b1010"])
    def test_numbers(self, src):
        parses(f"let x = {src}")

    @pytest.mark.parametrize("src", ['"hello"', "'world'"])
    def test_strings(self, src):
        parses(f"let x = {src}")

    @pytest.mark.parametrize("src", ["true", "false", "null"])
    def test_literals(self, src):
        parses(f"let x = {src}")

    @pytest.mark.parametrize("src", ["30s", "5min", "2h", "7d", "500ms"])
    def test_durations(self, src):
        parses(f"let x = {src}")

    @pytest.mark.parametrize("src", ["128Mi", "500m", "2Gi", "1Ti"])
    def test_resources(self, src):
        parses(f"let x = {src}")

    def test_percentage(self):
        parses("let x = 25%")

    def test_template_string(self):
        parses("let x = `hello {name}`")

    def test_template_with_expression(self):
        parses("let x = `{a + b} items`")

    def test_list(self):
        parses("let x = [1, 2, 3]")

    def test_map(self):
        parses("let x = {a: 1, b: 2}")

    def test_nested_map(self):
        parses("let x = {a: {b: 1}}")

    @pytest.mark.parametrize(
        "src", ["a + b", "a - b", "a * b", "a / b", "a % b", "a ** b"]
    )
    def test_arithmetic(self, src):
        parses(f"let x = {src}")

    @pytest.mark.parametrize(
        "src", ["a == b", "a != b", "a < b", "a <= b", "a > b", "a >= b"]
    )
    def test_comparisons(self, src):
        parses(f"let x = {src}")

    def test_logical(self):
        parses("let x = a && b || !c")

    def test_if_expr(self):
        parses("let x = if a then b else c")

    def test_match_expr(self):
        parses('let m = match status { 200 -> "ok" 404 -> "not found" _ -> "other" }')

    def test_call(self):
        parses("let x = foo(a, b)")

    def test_call_kwargs(self):
        parses("let x = foo(a, key = b)")

    def test_attribute(self):
        parses("let x = obj.field")

    def test_index(self):
        parses("let x = arr[0]")

    def test_chaining(self):
        parses("let x = obj.field[0].method()")


# --------------------------------------------------------------------------- #
# Variables & imports
# --------------------------------------------------------------------------- #


class TestVariablesAndImports:
    def test_let(self):
        parses("let x = 42")

    def test_const(self):
        parses('const NAME = "prod"')

    def test_let_env_call(self):
        parses('let x = env("FOO")')

    def test_import(self):
        parses('import "./other.infra"')

    def test_import_alias(self):
        parses('import "./other.infra" as other')

    def test_from_import(self):
        parses('from "./lib.infra" import foo, bar')


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


class TestEdgeCases:
    def test_empty_file(self):
        parses("")

    def test_only_comments(self):
        parses("# just a comment\n/* and a block */")

    def test_multiple_structures(self):
        parses('service a { image: "x" }\ndatabase b { type: postgres }')

    def test_comment_between_fields(self):
        parses('service a {\n  # comment\n  image: "x"\n  # another\n  replicas: 2\n}')

    def test_comment_after_value(self):
        parses('service a { image: "x"  # trailing comment\n}')

    def test_block_comment(self):
        parses('/* leading */\nservice a { image: "x" }')

    def test_long_identifiers(self):
        parses("let this_is_a_very_long_variable_name_that_should_work_fine = 1")

    def test_hyphen_identifiers(self):
        parses('service my-service { image: "x" }')

    def test_mixed_indentation(self):
        parses('service a {\n  image: "x"\n\t\treplicas: 2\n}')

    def test_no_trailing_newline(self):
        parses('service a { image: "x" }')

    def test_many_blank_lines(self):
        parses('service a { image: "x" }\n\n\n\n\ndatabase b { type: postgres }')


# --------------------------------------------------------------------------- #
# Parse errors
# --------------------------------------------------------------------------- #


class TestParseErrors:
    def test_unclosed_brace(self):
        err = fails('service a { image: "x" ')
        assert err.location is not None

    def test_missing_keyword(self):
        err = fails('a { image: "x" }')
        assert err.location is not None

    def test_missing_value_after_colon(self):
        err = fails("service a { image: }")
        assert err.location is not None

    def test_invalid_decorator(self):
        err = fails('@!prod\nservice a { image: "x" }')
        assert err.location is not None

    def test_unclosed_string(self):
        # an unterminated string is a *lexical* error
        from infra.errors.exceptions import InfraLexError

        with pytest.raises(InfraLexError):
            P.parse('service a { image: "unterminated }', filename="bad.infra")
