"""CLI command tests using typer.testing.CliRunner."""

from __future__ import annotations

import json
import os
import re
import shutil

import pytest
from pathlib import Path
from typer.testing import CliRunner

from infra.cli.main import app

runner = CliRunner()

# Click/Rich may emit ANSI escape codes in --help output depending on the
# terminal/version (e.g. some CI runners on Python 3.13). Strip them so
# assertions on help text are robust across environments.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def write_infra(path: Path, content: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


@pytest.fixture
def simple_service(tmp_path):
    return write_infra(tmp_path / "test.infra", 'service api { image: "nginx:1.25" replicas: 2 }')


@pytest.fixture
def invalid_service(tmp_path):
    return write_infra(tmp_path / "bad.infra", 'service api { image: "nginx" replicas: 0 }')


class TestCompileCommand:
    def test_compile_kubernetes_default(self, simple_service, tmp_path):
        out = tmp_path / "out"
        result = runner.invoke(app, ["compile", str(simple_service), "--output", str(out)])
        assert result.exit_code == 0, result.output
        assert len(list(out.glob("*.yaml"))) > 0

    def test_compile_compose(self, simple_service, tmp_path):
        out = tmp_path / "out"
        result = runner.invoke(app, ["compile", str(simple_service), "--target", "compose", "--output", str(out)])
        assert result.exit_code == 0, result.output
        assert len(list(out.glob("docker-compose*"))) > 0

    def test_compile_github(self, tmp_path):
        src = write_infra(tmp_path / "pipe.infra",
                          'pipeline ci { trigger { branches: ["main"] } '
                          'stages { t: { runsOn: "ubuntu-latest" steps { s: { run: "echo ok" } } } } }')
        out = tmp_path / "out"
        result = runner.invoke(app, ["compile", str(src), "--target", "github", "--output", str(out)])
        assert result.exit_code == 0, result.output

    def test_compile_terraform(self, tmp_path):
        src = write_infra(tmp_path / "tf.infra", "cluster main { provider: aws }")
        out = tmp_path / "out"
        result = runner.invoke(app, ["compile", str(src), "--target", "terraform", "--output", str(out)])
        assert result.exit_code == 0, result.output

    def test_compile_dry_run(self, simple_service, tmp_path):
        out = tmp_path / "dry-out"
        result = runner.invoke(app, ["compile", str(simple_service), "--dry-run", "--output", str(out)])
        assert result.exit_code == 0
        assert not out.exists()

    def test_compile_split(self, tmp_path):
        src = write_infra(tmp_path / "multi.infra",
                          'service api { image: "nginx:1.0" }\nservice worker { image: "redis:7" }')
        out = tmp_path / "out"
        result = runner.invoke(app, ["compile", str(src), "--split", "--output", str(out)])
        assert result.exit_code == 0
        assert len(list(out.glob("*.yaml"))) >= 2

    def test_compile_errors_exit_1(self, invalid_service, tmp_path):
        out = tmp_path / "out"
        result = runner.invoke(app, ["compile", str(invalid_service), "--output", str(out)],
                               catch_exceptions=False)
        assert result.exit_code == 1

    def test_compile_nonexistent_file(self):
        result = runner.invoke(app, ["compile", "does_not_exist.infra"])
        assert result.exit_code != 0

    def test_compile_var_option(self, simple_service, tmp_path):
        out = tmp_path / "out"
        result = runner.invoke(app, ["compile", str(simple_service), "--var", "ENV=prod", "--output", str(out)])
        assert result.exit_code == 0


class TestValidateCommand:
    def test_validate_valid(self, simple_service):
        result = runner.invoke(app, ["validate", str(simple_service)])
        assert result.exit_code == 0

    def test_validate_invalid_exits_1(self, invalid_service):
        result = runner.invoke(app, ["validate", str(invalid_service)], catch_exceptions=False)
        assert result.exit_code == 1

    def test_validate_shows_error(self, invalid_service):
        result = runner.invoke(app, ["validate", str(invalid_service)], catch_exceptions=False)
        assert "replicas" in result.output.lower() or "E0" in result.output

    def test_validate_json_format(self, invalid_service):
        result = runner.invoke(app, ["validate", str(invalid_service), "--format", "json"],
                               catch_exceptions=False)
        data = json.loads(result.output)
        assert "valid" in data
        assert data["valid"] is False
        assert "errors" in data
        assert len(data["errors"]) > 0

    def test_validate_github_format(self, invalid_service):
        result = runner.invoke(app, ["validate", str(invalid_service), "--format", "github"],
                               catch_exceptions=False)
        assert "::error" in result.output

    def test_validate_json_flag_valid(self, simple_service):
        result = runner.invoke(
            app, ["validate", str(simple_service), "--json"], catch_exceptions=False
        )
        data = json.loads(result.output)
        assert data["valid"] is True
        assert data["file"]
        assert data["errors"] == []
        assert "severity" in str(data)

    def test_validate_json_flag_invalid(self, invalid_service):
        result = runner.invoke(
            app, ["validate", str(invalid_service), "--json"], catch_exceptions=False
        )
        data = json.loads(result.output)
        assert data["valid"] is False
        assert len(data["errors"]) > 0
        first = data["errors"][0]
        for key in ("code", "message", "line", "column", "severity", "hint"):
            assert key in first

    def test_validate_strict_warnings_as_errors(self, tmp_path):
        src = write_infra(tmp_path / "w.infra", 'let unused_var = "hello"\nservice api { image: "nginx:1.0" }')
        result = runner.invoke(app, ["validate", str(src), "--strict"], catch_exceptions=False)
        assert result.exit_code == 1

    def test_validate_multiple_files(self, tmp_path):
        f1 = write_infra(tmp_path / "a.infra", 'service a { image: "nginx:1.0" }')
        f2 = write_infra(tmp_path / "b.infra", 'service b { image: "redis:7" }')
        result = runner.invoke(app, ["validate", str(f1), str(f2)])
        assert result.exit_code == 0


class TestFmtCommand:
    def test_fmt_modifies_file(self, tmp_path):
        src = write_infra(tmp_path / "fmt.infra", 'service api{image:"nginx:1.0"}')
        result = runner.invoke(app, ["fmt", str(src)])
        assert result.exit_code == 0
        assert "image:" in src.read_text(encoding="utf-8")

    def test_fmt_check_unformatted_exits_1(self, tmp_path):
        src = write_infra(tmp_path / "fmt.infra", 'service api{image:"nginx:1.0"}')
        result = runner.invoke(app, ["fmt", str(src), "--check"], catch_exceptions=False)
        assert result.exit_code == 1

    def test_fmt_idempotent(self, tmp_path):
        src = write_infra(tmp_path / "fmt.infra", 'service api { image: "nginx:1.0" }')
        runner.invoke(app, ["fmt", str(src)])
        first = src.read_text(encoding="utf-8")
        runner.invoke(app, ["fmt", str(src)])
        assert src.read_text(encoding="utf-8") == first


class TestCheckCommand:
    def test_check_valid_syntax(self, simple_service):
        result = runner.invoke(app, ["check", str(simple_service)])
        assert result.exit_code == 0

    def test_check_invalid_syntax(self, tmp_path):
        src = write_infra(tmp_path / "bad.infra", 'service { image: "nginx" }')
        result = runner.invoke(app, ["check", str(src)], catch_exceptions=False)
        assert result.exit_code == 1


class TestInitCommand:
    def test_init_creates_structure(self, tmp_path):
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = runner.invoke(app, ["init", "myproject", "--yes"])
            assert result.exit_code == 0, result.output
            assert (tmp_path / "myproject" / "infra" / "main.infra").exists()
            assert (tmp_path / "myproject" / ".infra-config.yaml").exists()
        finally:
            os.chdir(cwd)

    def test_init_generated_valid_infra(self, tmp_path):
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            runner.invoke(app, ["init", "proj", "--yes"])
            main = tmp_path / "proj" / "infra" / "main.infra"
            if main.exists():
                from infra import parse, validate

                prog = parse(main.read_text(encoding="utf-8"))
                res = validate(prog)
                assert len(res.errors) == 0
        finally:
            os.chdir(cwd)


class TestLspCliCommand:
    """`infra lsp` is a registered Typer command independent of pygls.

    The ``lsp_cmd`` callback lazily imports pygls only when actually starting
    the server, so ``--help`` (which never invokes the callback) must work
    even if pygls is absent. This keeps the CLI-registration contract
    deterministic across environments (pygls 1.x / 2.x / not installed).
    """

    def test_lsp_command_registered(self):
        result = runner.invoke(app, ["lsp", "--help"])
        assert result.exit_code == 0
        output = _strip_ansi(result.output)
        assert "--tcp" in output
        assert "--port" in output
        assert "--host" in output
        # --help must not import pygls (callback is lazy)
        assert "pygls" not in output

    def test_lsp_command_listed_in_app(self):
        names = [c.name for c in app.registered_commands]
        assert "lsp" in names


class TestErrorReporter:
    def test_reporter_formats_semantic_errors(self, invalid_service):
        from infra import parse, validate
        from infra.errors.reporter import ErrorReporter

        source = invalid_service.read_text(encoding="utf-8")
        result = validate(parse(source))
        output = ErrorReporter().report_semantic_errors(result.errors, result.warnings, source)
        assert isinstance(output, str)
        assert len(output) > 0

    def test_reporter_suggest_similar(self):
        from infra.errors.reporter import suggest_similar

        assert suggest_similar("postgress", ["postgres", "mysql"]) == "postgres"

    def test_reporter_no_suggestion_gibberish(self):
        from infra.errors.reporter import suggest_similar

        assert suggest_similar("xyzxyzxyz", ["postgres", "mysql"]) is None

    def test_reporter_format_as_json(self, invalid_service):
        from infra import parse, validate
        from infra.errors.reporter import ErrorReporter

        result = validate(parse(invalid_service.read_text(encoding="utf-8")))
        data = json.loads(ErrorReporter().format_as_json(result))
        assert "errors" in data
        assert "valid" in data


class TestTokensModule:
    def test_keywords_present(self):
        from infra.lexer.tokens import KEYWORDS

        assert "service" in KEYWORDS
        assert "database" in KEYWORDS
        assert "pipeline" in KEYWORDS
        assert len(KEYWORDS) > 10

    def test_is_keyword(self):
        from infra.lexer.tokens import is_keyword

        assert is_keyword("service")
        assert not is_keyword("myservice")
        assert not is_keyword("foobar")

    def test_get_token_description(self):
        from infra.lexer.tokens import TokenType, get_token_description

        for tt in TokenType:
            assert isinstance(get_token_description(tt), str)

    def test_tokentype_enum_complete(self):
        from infra.lexer.tokens import TokenType

        assert len(list(TokenType)) >= 20


class TestTyperCliOptions:
    """Regression: Typer option params (`--environment`, `--project`,
    `--no-color`) are part of the CLI surface and must not be removed as
    "dead code". Vulture flags them because it doesn't understand Typer; these
    tests assert the flags actually work.
    """

    def test_compile_environment_flag(self, simple_service, tmp_path):
        out = tmp_path / "out"
        result = runner.invoke(
            app,
            ["compile", str(simple_service), "--environment", "prod", "--output", str(out)],
        )
        assert result.exit_code == 0
        assert out.exists()

    def test_feedback_project_flag(self):
        result = runner.invoke(app, ["feedback", "--project"])
        assert result.exit_code == 0
        assert "feedback" in result.output.lower() or "config" in result.output.lower()

    def test_no_color_flag(self):
        result = runner.invoke(app, ["--no-color", "--help"])
        assert result.exit_code == 0
        assert "compile" in result.output

    def test_compile_environment_flag_dry_run(self, simple_service):
        result = runner.invoke(
            app,
            ["compile", str(simple_service), "--environment", "staging", "--dry-run"],
        )
        assert result.exit_code == 0
