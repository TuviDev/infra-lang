"""Completeness: InfraPrinter / format_source / format_file.

Covers every definition kind, every expression kind, round-trip
parse->fmt->parse, idempotency, empty blocks and golden output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infra import parse
from infra.cli.printer import InfraPrinter, format_file, format_source


def fmt(src: str) -> str:
    return format_source(src)


class TestServiceFormatting:
    def test_image_and_replicas(self):
        out = fmt('service api { image: "img:1" replicas: 3 }')
        assert 'image: "img:1"' in out
        assert "replicas: 3" in out

    def test_build_block(self):
        out = fmt('service api { build { context: "." dockerfile: "D" } }')
        assert "build {" in out and "context: ." in out and "dockerfile: D" in out

    def test_port_host_target(self):
        out = fmt("service api { image: \"x\" port 8080:80 }")
        assert "port 8080:80" in out

    def test_port_single(self):
        out = fmt('service api { image: "x" port 8080 }')
        assert "port 8080" in out

    def test_env_variants(self):
        out = fmt(
            'service api { image: "x" env { '
            'A: "b" X: from secret "s".k Y: from config "c".v Z: from env "E" } }'
        )
        assert 'A: "b"' in out
        assert 'X: from secret "s".k' in out
        assert 'Y: from config "c".v' in out
        assert 'Z: from env "E"' in out

    def test_depends(self):
        out = fmt('service api { image: "x" depends: ["db", "cache"] }')
        assert "depends: [db, cache]" in out

    def test_resources(self):
        out = fmt(
            'service api { image: "x" resources { '
            'requests { cpu: 100m, memory: 128Mi } limits { cpu: 1, memory: 512Mi } } }'
        )
        assert "requests: {cpu: 100m, memory: 128Mi}" in out
        assert "limits: {cpu: 1, memory: 512Mi}" in out

    def test_health(self):
        out = fmt('service api { image: "x" health http("/health") }')
        assert 'health http("/health")' in out

    def test_decorator(self):
        out = fmt('@prod\nservice api { image: "x" }')
        assert "@prod" in out

    def test_image_identifier(self):
        out = fmt('const IMG = "x"\nservice api { image: IMG }')
        assert "image: IMG" in out


class TestDatabaseFormatting:
    def test_all_fields(self):
        out = fmt(
            'database db { type: postgres version: "15" replicas: 3 ha: true '
            'storage: 20Gi backup { enabled: true schedule: "0 2 * * *" } '
            'users { app: "pw" } }'
        )
        assert "type: postgres" in out
        assert "version: 15" in out
        assert "replicas: 3" in out
        assert "ha: true" in out
        assert "backup {" in out
        assert 'schedule: "0 2 * * *"' in out
        assert "users {" in out

    def test_minimal(self):
        out = fmt("database db { type: postgres }")
        assert "type: postgres" in out


class TestCacheQueueStorageNetwork:
    def test_cache(self):
        out = fmt('cache c { type: redis version: "7" maxmemory: 128Mi policy: "x" persistence: true replicas: 2 }')
        assert "maxmemory: 128Mi" in out and "persistence: true" in out and "replicas: 2" in out

    def test_queue_topics(self):
        out = fmt('queue q { type: kafka topics { t: { partitions: 3, replication: 2 } } }')
        assert "topics {" in out and "partitions: 3" in out and "replication: 2" in out

    def test_storage(self):
        out = fmt('storage s { type: s3 bucket: "b" region: "r" size: 10Gi }')
        assert "bucket: b" in out and "region: r" in out

    def test_network(self):
        out = fmt('network n { cidr: "10.0.0.0/16" subnets { a: { cidr: "1.1.1.1" } } }')
        assert 'cidr: "10.0.0.0/16"' in out and "subnets {" in out


class TestSecretConfig:
    def test_secret_sources(self):
        out = fmt('secret s { a: from vault "v" b: from env "E" c: from file "f" d: "plain" }')
        assert 'a: from vault "v"' in out
        assert 'b: from env "E"' in out
        assert 'c: from file "f"' in out
        assert "d: plain" in out

    def test_config(self):
        out = fmt('config c { a: 1 b: true c: "x" }')
        assert "a: 1" in out and "b: true" in out and 'c: "x"' in out


class TestEnvironmentCluster:
    def test_environment(self):
        out = fmt('environment dev { provider: aws region: "eu" namespace: "ns" }')
        assert "provider: aws" in out and "region: eu" in out and "namespace: ns" in out

    def test_cluster_nodes(self):
        out = fmt('cluster c { provider: aws nodes { w: { machine type: "t3" min: 1 max: 5 } } }')
        assert "nodes {" in out
        assert "machine type: t3" in out


class TestExpressionFormatting:
    def test_unary(self):
        assert fmt("let a = -x") == "let a = -x\n"

    def test_call_kwargs(self):
        out = fmt("let b = foo(a, key = 2)")
        assert "key = 2" in out

    def test_index(self):
        assert fmt("let c = arr[0]") == "let c = arr[0]\n"

    def test_template(self):
        out = fmt("let d = `a {x} b`")
        assert "`a {x} b`" in out

    def test_if_expr(self):
        out = fmt("let e = if a then b else c")
        assert "if a then b else c" in out

    def test_match_expr(self):
        out = fmt('let f = match s { 1 -> "a" _ -> "b" }')
        assert "match s {" in out and '1 -> "a"' in out and '_ -> "b"' in out

    def test_percentage(self):
        assert fmt("let g = 50%") == "let g = 50%\n"

    def test_multiline_list(self):
        out = fmt("let x = [1, 2, 3, 4, 5, 6]")
        assert "[1, 2, 3, 4, 5, 6]" not in out  # exceeds inline limit

    def test_inline_list(self):
        assert fmt("let x = [1, 2]") == "let x = [1, 2]\n"

    def test_literals(self):
        assert fmt("let a = 42") == "let a = 42\n"
        assert fmt("let b = true") == "let b = true\n"
        assert fmt("let c = null") == "let c = null\n"
        assert fmt("let d = 3.14") == "let d = 3.14\n"
        assert fmt('let e = "hi"') == 'let e = "hi"\n'

    def test_attribute(self):
        assert fmt("let x = obj.field") == "let x = obj.field\n"


class TestImportFormatting:
    def test_plain(self):
        assert fmt('import "./x.infra"') == 'import "./x.infra"\n'

    def test_alias(self):
        assert fmt('import "./x.infra" as lib') == 'import "./x.infra" as lib\n'

    def test_from_names(self):
        assert fmt('from "./x.infra" import A, B') == 'from "./x.infra" import A, B\n'


class TestRoundTrip:
    @pytest.mark.parametrize("src", [
        'service api { image: "img:1" replicas: 3 port 8080 health http("/h") }',
        'database db { type: postgres version: "15" backup { enabled: true } }',
        'cache c { type: redis maxmemory: 128Mi }',
        'queue q { type: kafka topics { t: { partitions: 3 } } }',
        'storage s { type: s3 bucket: "b" }',
        'network n { cidr: "10.0.0.0/16" subnets { a: { cidr: "1.1.1.1" } } }',
        'secret s { a: from vault "v" b: "plain" }',
        'config c { a: 1 b: true }',
        'environment dev { provider: aws namespace: "ns" }',
        'cluster c { provider: aws nodes { w: { machine type: "t3" } } }',
        'pipeline ci { trigger { branches: ["main"] } stages { t: { steps { s: { run: "x" } } } } }',
    ])
    def test_round_trip_parses(self, src):
        # format then re-parse must not raise
        formatted = fmt(src)
        parse(formatted)  # must not raise (cached parser)


class TestIdempotency:
    @pytest.mark.parametrize("src", [
        'service api { image: "img:1" replicas: 3 }',
        'database db { type: postgres }',
        'cache c { type: redis }',
        'queue q { type: rabbitmq }',
        'storage s { type: s3 }',
        'network n { cidr: "1.1.1.1" }',
        'secret s { a: "b" }',
        'config c { a: 1 }',
        'environment dev { }',
        'cluster c { }',
        'pipeline p { stages { t: { steps { s: { run: "x" } } } } }',
        'let x = [1, 2, 3, 4, 5, 6]',
    ])
    def test_format_twice_same(self, src):
        once = fmt(src)
        assert fmt(once) == once


class TestEmptyBlocks:
    def test_empty_service(self):
        out = fmt('service api { }')
        assert out == "service api {\n}\n"

    def test_empty_pipeline(self):
        out = fmt("pipeline ci { }")
        assert "pipeline ci {" in out


class TestGolden:
    def test_hello_world_golden(self):
        out = fmt('service hello { image: "nginx:1.25" port: 80 }')
        assert out == (
            "service hello {\n"
            "    image: \"nginx:1.25\"\n"
            "    port 80\n"
            "}\n"
        )

    def test_database_golden(self):
        out = fmt('database db { type: postgres version: "15" }')
        assert out == (
            "database db {\n"
            "    type: postgres\n"
            "    version: 15\n"
            "}\n"
        )


class TestFormatFile:
    def test_format_file_changed(self, tmp_path):
        p = tmp_path / "f.infra"
        p.write_text('service api { image: "x" }')
        formatted, changed = format_file(p)
        assert changed is True
        assert "service api {" in formatted

    def test_format_file_unchanged(self, tmp_path):
        p = tmp_path / "f.infra"
        content = 'service api {\n    image: "x"\n}\n'
        p.write_text(content)
        formatted, changed = format_file(p)
        assert changed is False
        assert formatted == content


class TestPrinterInternals:
    def test_indent_custom(self):
        out = InfraPrinter(indent=2).print(parse('service api { image: "x" }'))
        assert "  image: \"x\"" in out

    def test_block_helper(self):
        pr = InfraPrinter()
        pr._block("head", ["a", "b"])
        assert pr.out[0] == "head {"

    def test_pattern_wildcard(self):
        assert InfraPrinter._pattern(None) == "_"

    def test_num_float(self):
        assert InfraPrinter._num(2.5) == "2.5"

    def test_num_int(self):
        assert InfraPrinter._num(2.0) == "2"

    def test_str_list_non_list(self):
        pr = InfraPrinter()
        assert pr._str_list(["a", "b"]) == ["a", "b"]
