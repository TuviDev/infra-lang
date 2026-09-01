"""Edge-case tests for infra.cli.printer (v0.5.3).

Targets previously-uncovered branches: ``_qstr`` quoting, rare ``_expr``
node kinds (Percentage/BinaryOp/UnaryOp/IfExpr/MatchExpr/TemplateString,
long List/Map), rare ``_stmt`` kinds (import variants, storage, network,
secret sources, secret_store, custom resource, pipeline, environment def,
cluster) and the ``str`` fallbacks.
"""

from __future__ import annotations

from infra.cli.printer import InfraPrinter, format_source
from infra.parser import ast_nodes as n

BT = chr(96)  # backtick — template strings


class TestQstr:
    def test_none_renders_empty_quotes(self) -> None:
        from infra.cli.printer import _qstr

        assert _qstr(None) == '""'

    def test_bareable_stays_bare(self) -> None:
        from infra.cli.printer import _qstr

        assert _qstr("daily") == "daily"

    def test_unsafe_is_quoted(self) -> None:
        from infra.cli.printer import _qstr

        assert _qstr("0 2 * * *") == '"0 2 * * *"'


class TestExprRareKinds:
    def _expr(self, node) -> str:
        return InfraPrinter()._expr(node)

    def test_none_expression(self) -> None:
        assert self._expr(None) == ""

    def test_literal_none(self) -> None:
        assert self._expr(n.Literal(None)) == "null"

    def test_bool_literal(self) -> None:
        assert self._expr(n.Literal(True)) == "true"

    def test_percentage(self) -> None:
        assert self._expr(n.Percentage(50.0)) == "50%"

    def test_binary_op(self) -> None:
        out = self._expr(n.BinaryOp(n.Literal(8), "+", n.Literal(2)))
        assert out == "8 + 2"

    def test_unary_op(self) -> None:
        assert self._expr(n.UnaryOp("-", n.Literal(5))) == "-5"

    def test_unknown_node_str_fallback(self) -> None:
        assert self._expr(object())  # any object is rendered via str()


class TestLongCollections:
    def test_four_item_list_goes_multiline(self) -> None:
        out = format_source("let x = [1, 2, 3, 4]")
        assert "[\n" in out

    def test_four_entry_map_goes_multiline(self) -> None:
        out = format_source("let x = { a: 1, b: 2, c: 3, d: 4 }")
        assert "{\n" in out


class TestComplexExpressions:
    def test_template_string_renders_backticks(self) -> None:
        out = format_source(f"let u = {BT}http://{{host}}:9090/{BT}")
        assert BT + "http://{host}:9090/" + BT in out

    def test_if_expr(self) -> None:
        out = format_source("let i = if c then 1 else 2")
        assert "if c then 1 else 2" in out

    def test_match_expr_with_patterns(self) -> None:
        out = format_source('let m = match x { 1 -> "a" _ -> "b" 2 -> "c" }')
        assert "match x {" in out
        assert '1 -> "a"' in out
        assert '_ -> "b"' in out
        assert '2 -> "c"' in out

    def test_binary_and_unary_from_source(self) -> None:
        assert "-5" in format_source("let n = -5")
        assert "1 + 2" in format_source("let b = 1 + 2")

    def test_percentage_from_source(self) -> None:
        assert "50%" in format_source("let p = 50%")


class TestImportVariants:
    def test_from_import_names(self) -> None:
        out = format_source('from "common.infra" import db, cache')
        assert 'from "common.infra" import db, cache' in out

    def test_import_alias(self) -> None:
        out = format_source('import "lib.infra" as lib')
        assert 'import "lib.infra" as lib' in out

    def test_plain_import(self) -> None:
        out = format_source('import "base.infra"')
        assert 'import "base.infra"' in out


class TestRareStatements:
    def test_storage_def(self) -> None:
        out = format_source('storage assets { type: s3 bucket: "b" region: "eu" }')
        assert "storage assets {" in out

    def test_network_with_subnets(self) -> None:
        out = format_source(
            'network lan { cidr: "10.0.0.0/8" '
            'subnets { web: { cidr: "10.1.0.0/16" } } }'
        )
        assert "network lan {" in out
        assert "subnets {" in out

    def test_secret_sources(self) -> None:
        out = format_source(
            "secret db-creds { "
            'A: from vault "path/to" B: from env "B_VAR" C: from file "f.txt" }'
        )
        assert 'A: from vault "path/to"' in out
        assert 'B: from env "B_VAR"' in out
        assert 'C: from file "f.txt"' in out

    def test_secret_store_full_fields(self) -> None:
        out = format_source(
            'secret_store "vault" { provider: "vault" address: "https://v" '
            'path: "secret/" region: "eu-1" namespace: "ns" project: "p1" '
            'foo: "bar" }'
        )
        assert 'provider: "vault"' in out
        assert 'path: "secret/"' in out
        assert 'region: "eu-1"' in out
        assert 'namespace: "ns"' in out
        assert 'project: "p1"' in out
        assert 'foo: "bar"' in out

    def test_custom_resource(self) -> None:
        out = format_source('resource "widget" "w1" { replicas: 3 }')
        assert 'resource "widget" "w1"' in out

    def test_config_def(self) -> None:
        out = format_source('config app { log_level: "info" }')
        assert "config app {" in out

    def test_pipeline_full(self) -> None:
        out = format_source(
            "pipeline ci { "
            'trigger { branches: ["main"] schedule: "0 0 * * *" manual: true } '
            "stages { "
            'build: { runsOn: "ubuntu-latest" steps { '
            't: { run: "pytest" } l: { uses: "actions/lint@v1" } } } '
            "deploy: { "
            "needs: [build] "
            'steps { d: { run: "make deploy" } } } } }'
        )
        assert "pipeline ci {" in out
        assert "schedule:" in out
        assert "manual: true" in out
        assert "runsOn:" in out
        assert "needs: [build]" in out
        assert '{ run: "pytest" }' in out
        assert '{ uses: "actions/lint@v1" }' in out

    def test_environment_def(self) -> None:
        out = format_source(
            'environment prod { provider: "aws" region: "eu-1" '
            'namespace: "myapp-prod" }'
        )
        assert "environment prod {" in out
        # provider/region/namespace are bare identifiers in the AST
        assert "provider: aws" in out
        assert "region: eu-1" in out
        assert "namespace: myapp-prod" in out

    def test_cluster_with_nodes(self) -> None:
        out = format_source(
            "cluster main { provider: aws nodes { "
            'w: { machine type: "t3.medium" min: 1 max: 5 } } }'
        )
        assert "cluster main {" in out
        assert "nodes {" in out

    def test_variable_decls(self) -> None:
        out = format_source('let x = 5\nconst Y = "s"')
        assert "let x = 5" in out
        assert 'const Y = "s"' in out


class TestServiceEdgeBranches:
    def test_image_from_expression(self) -> None:
        out = format_source("service api { image: 1 + 2 }")
        assert "image: 1 + 2" in out

    def test_build_context_only(self) -> None:
        out = format_source('service api { build { context: "." } }')
        assert "context: ." in out
        assert "dockerfile" not in out

    def test_depends_on_rendered(self) -> None:
        out = format_source('service api { image: "a" depends_on: [db] }')
        assert "depends_on: [db]" in out

    def test_resources_requests_only(self) -> None:
        out = format_source(
            'service api { image: "a" resources { requests { cpu: 100m } } }'
        )
        assert "requests: {cpu: 100m}" in out
        assert "limits" not in out


class TestDatabaseEdgeBranches:
    def test_minimal_database(self) -> None:
        out = format_source('database db { engine: "postgres" }')
        assert "database db {" in out

    def test_backup_schedule_without_enabled(self) -> None:
        out = format_source(
            'database db { engine: "postgres" backup { schedule: "0 2 * * *" } }'
        )
        assert "backup {" in out
        assert 'schedule: "0 2 * * *"' in out
        assert "enabled: true" not in out

    def test_user_without_password_renders_empty_quotes(self) -> None:
        out = format_source('database db { engine: "postgres" users { app: "" } }')
        assert 'app: ""' in out


class TestPatternFallbacks:
    def test_literal_string_pattern(self) -> None:
        out = InfraPrinter._pattern(n.Literal("y"))
        assert out == "y"

    def test_identifier_pattern(self) -> None:
        assert InfraPrinter._pattern(n.Identifier("x")) == "x"

    def test_none_pattern_is_wildcard(self) -> None:
        assert InfraPrinter._pattern(None) == "_"

    def test_unknown_pattern_str_fallback(self) -> None:
        assert InfraPrinter._pattern(42) == "42"


class TestStatementFallback:
    def test_unknown_statement_str(self) -> None:
        out = InfraPrinter()._stmt(n.Literal("x"))
        assert isinstance(out, str)
        assert "Literal" in out or "x" in out
