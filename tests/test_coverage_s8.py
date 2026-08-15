"""Coverage-boost tests for Session 8: base evaluator and CLI helpers."""

from __future__ import annotations

from infra.backends.base import CompileContext, evaluate_expression
from infra.parser import ast_nodes as n
from infra.parser import parse


def ctx():
    return CompileContext(program=n.Program(), symbol_table=None)


class TestEvaluateExpressionPaths:
    def test_percentage(self):
        assert evaluate_expression(n.Percentage(50), ctx()) == "50%"

    def test_binary_ops(self):
        c = ctx()
        for op, expected in [("+", 3), ("-", 1), ("*", 2), ("/", 2.0), ("==", False), ("!=", True)]:
            e = n.BinaryOp(n.Literal(2), op, n.Literal(1))
            assert evaluate_expression(e, c) == expected

    def test_binary_type_error_returns_none(self):
        c = ctx()
        e = n.BinaryOp(n.Literal("a"), "-", n.Literal("b"))
        assert evaluate_expression(e, c) is None

    def test_identifier_missing_placeholder(self):
        assert evaluate_expression(n.Identifier("X"), ctx()) == "${X}"

    def test_template_with_expr(self):
        c = ctx()
        c.variables["T"] = n.Literal("v1")
        ts = n.TemplateString(parts=("img:", ("expr", "T")))
        assert evaluate_expression(ts, c) == "img:v1"

    def test_eval_builtin_secret(self):
        c = ctx()
        call = n.Call(callee=n.Identifier("secret"), args=(n.Literal("db"),))
        assert evaluate_expression(call, c) == "db"

    def test_eval_builtin_config(self):
        c = ctx()
        call = n.Call(callee=n.Identifier("config"), args=(n.Literal("app"),))
        assert evaluate_expression(call, c) == "app"

    def test_eval_builtin_version(self):
        c = ctx()
        call = n.Call(callee=n.Identifier("version"), args=(n.Literal("1.0"),))
        assert evaluate_expression(call, c) == "1.0"

    def test_unknown_expr_returns_none(self):
        assert evaluate_expression(object(), ctx()) is None  # type: ignore

    def test_evaluate_duration_float(self):
        from infra.backends.base import evaluate_duration

        assert evaluate_duration(n.Duration(1.5, "s")) == "1.5s"

    def test_evaluate_resource_default(self):
        from infra.backends.base import evaluate_resource

        assert evaluate_resource(n.ResourceValue(128, "Mi")) == "128Mi"


class TestGraphCLI:
    def test_graph_lists_services(self, tmp_path):
        from typer.testing import CliRunner

        from infra.cli.main import app

        f = tmp_path / "t.infra"
        f.write_text('service a { image: "x:1" }\nservice b { image: "y:1" depends: ["a"] }')
        r = CliRunner().invoke(app, ["graph", str(f)])
        assert r.exit_code == 0
        assert "a" in r.output and "b" in r.output

    def test_graph_empty_output(self, tmp_path):
        from typer.testing import CliRunner

        from infra.cli.main import app

        f = tmp_path / "t.infra"
        f.write_text("config c { a: 1 }")
        r = CliRunner().invoke(app, ["graph", str(f)])
        assert r.exit_code == 0


class TestDocsCLI:
    def test_docs_output(self, tmp_path):
        from typer.testing import CliRunner

        from infra.cli.main import app

        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x:1" }')
        out = tmp_path / "docs.md"
        r = CliRunner().invoke(app, ["docs", str(f), "--output", str(out)])
        assert r.exit_code == 0
        assert out.exists()
        assert "api" in out.read_text()

    def test_docs_stdout(self, tmp_path):
        from typer.testing import CliRunner

        from infra.cli.main import app

        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x:1" }')
        r = CliRunner().invoke(app, ["docs", str(f)])
        assert r.exit_code == 0
        assert "api" in r.output


class TestCompileOnceWatch:
    def _console(self):
        class C:
            def print(self, *a, **k):
                pass

        return C()

    def test_success_compiles(self, tmp_path):
        from infra.cli.compile import _compile_once_watch

        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x:1" }')
        out = tmp_path / "out"
        ok, ms, watched = _compile_once_watch(
            f, "kubernetes", out, False, {}, False, self._console()
        )
        assert ok is True
        assert out.exists()

    def test_invalid_returns_false(self, tmp_path):
        from infra.cli.compile import _compile_once_watch

        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x" replicas: 0 }')
        ok, ms, watched = _compile_once_watch(
            f, "kubernetes", tmp_path / "o", False, {}, False, self._console()
        )
        assert ok is False

    def test_exception_returns_false(self, tmp_path):
        from infra.cli.compile import _compile_once_watch

        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x:1" }')
        ok, ms, watched = _compile_once_watch(
            f, "nonexistent-backend", tmp_path / "o", False, {}, False, self._console()
        )
        assert ok is False


class TestWatchHelpers:
    def test_collect_watched_files_no_imports(self, tmp_path):
        from infra.cli.compile import _collect_watched_files

        from infra.parser import parse

        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x:1" }')
        prog = parse(f.read_text())
        files = _collect_watched_files(f, prog)
        assert f.resolve() in files

    def test_collect_watched_files_with_import(self, tmp_path):
        from infra.cli.compile import _collect_watched_files

        from infra.parser import parse

        lib = tmp_path / "lib.infra"
        lib.write_text('const X = "1"')
        f = tmp_path / "t.infra"
        f.write_text('import "./lib.infra"\nservice api { image: X }')
        prog = parse(f.read_text())
        files = _collect_watched_files(f, prog)
        assert lib.resolve() in files
