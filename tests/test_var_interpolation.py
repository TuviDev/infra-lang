"""--var CLI interpolation tests."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from infra import parse
from infra.backends.base import CompileContext
from infra.backends.compose import DockerComposeBackend
from infra.backends.kubernetes import KubernetesBackend
from infra.cli.main import app
from infra.parser.ast_nodes import Literal

runner = CliRunner()


def write(tmp_path, content, name="t.infra"):
    f = tmp_path / name
    f.write_text(content)
    return f


def k8s_content(source, cli_vars=None):
    p = parse(source)
    result = KubernetesBackend().compile(p, cli_vars=cli_vars or {})
    return "\n".join(result.files.values())


class TestCompileContextVar:
    def test_cli_var_added_to_context(self):
        p = parse('service api { image: "nginx:1.0" }')
        ctx = CompileContext.from_program(p, cli_vars={"MY_KEY": "my_value"})
        var = ctx.variables.get("MY_KEY")
        assert var is not None
        assert isinstance(var, Literal)
        assert var.value == "my_value"

    def test_cli_var_overrides_const(self):
        p = parse('const TAG = "original"')
        ctx = CompileContext.from_program(p, cli_vars={"TAG": "override"})
        var = ctx.variables.get("TAG")
        assert isinstance(var, Literal)
        assert var.value == "override"

    def test_no_cli_vars_is_safe(self):
        p = parse('service api { image: "nginx:1.0" }')
        assert CompileContext.from_program(p).variables is not None

    def test_empty_cli_vars_is_safe(self):
        p = parse('service api { image: "nginx:1.0" }')
        assert CompileContext.from_program(p, cli_vars={}).variables is not None


class TestVarInBackend:
    def test_var_in_image_template(self):
        content = k8s_content('service api { image: `myapp:{ENV}` }', {"ENV": "production"})
        assert "myapp:production" in content
        assert "{ENV}" not in content

    def test_var_not_set_gives_placeholder(self):
        content = k8s_content('service api { image: `myapp:{MISSING}` }', {})
        assert "myapp:" in content

    def test_multiple_vars_interpolated(self):
        content = k8s_content(
            'service api { image: `{ORG}/{REPO}:{TAG}` }',
            {"ORG": "myorg", "REPO": "app", "TAG": "v2"},
        )
        assert "myorg/app:v2" in content

    def test_var_in_compose_backend(self):
        p = parse('service api { image: `app:{VERSION}` }')
        result = DockerComposeBackend().compile(p, cli_vars={"VERSION": "v1.5"})
        content = "\n".join(result.files.values())
        assert "app:v1.5" in content

    def test_const_without_var_still_works(self):
        content = k8s_content('const VERSION = "v1.0.0"\nservice api { image: `nginx:{VERSION}` }', {})
        assert "nginx:v1.0.0" in content


class TestVarCLI:
    def test_single_var_cli(self, tmp_path):
        f = write(tmp_path, 'service api { image: `app:{ENV}` }')
        out = tmp_path / "out"
        r = runner.invoke(app, ["compile", str(f), "--var", "ENV=staging", "--output", str(out)])
        assert r.exit_code == 0, r.output
        content = "\n".join(p.read_text() for p in out.rglob("*.yaml"))
        assert "app:staging" in content

    def test_multiple_vars_cli(self, tmp_path):
        f = write(tmp_path, 'service api { image: `{ORG}/{APP}:{TAG}` }')
        out = tmp_path / "out"
        r = runner.invoke(app, [
            "compile", str(f),
            "--var", "ORG=acme", "--var", "APP=api", "--var", "TAG=v3.0",
            "--output", str(out),
        ])
        assert r.exit_code == 0
        content = "\n".join(p.read_text() for p in out.rglob("*.yaml"))
        assert "acme/api:v3.0" in content

    def test_var_without_equals_ignored(self, tmp_path):
        f = write(tmp_path, 'service api { image: "nginx:1.0" }')
        out = tmp_path / "out"
        r = runner.invoke(app, ["compile", str(f), "--var", "NOEQUALS", "--output", str(out)])
        assert r.exit_code == 0

    def test_var_in_validate_command(self, tmp_path):
        f = write(tmp_path, 'service api { image: `app:{ENV}` }')
        r = runner.invoke(app, ["validate", str(f), "--var", "ENV=prod"])
        assert r.exit_code == 0
