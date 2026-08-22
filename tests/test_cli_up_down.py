"""Tests for `infra up` and `infra down` commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from infra.cli.main import app

runner = CliRunner()

SRC = 'service api { image: "nginx:1.25" port 80 replicas: 2 }'


def write(path: Path, content: str = SRC) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestUpDryRun:
    def test_up_dry_run_kubernetes(self, tmp_path, monkeypatch):
        src = write(tmp_path / "app.infra")
        result = runner.invoke(
            app, ["up", str(src), "--dry-run", "-t", "kubernetes"]
        )
        assert result.exit_code == 0
        assert "kubectl apply" in result.stdout
        assert "Dry run" in result.stdout

    def test_up_dry_run_compose(self, tmp_path, monkeypatch):
        src = write(tmp_path / "app.infra")
        result = runner.invoke(
            app, ["up", str(src), "--dry-run", "-t", "compose"]
        )
        assert result.exit_code == 0
        assert "docker compose" in result.stdout
        assert "up -d" in result.stdout

    def test_up_dry_run_helm(self, tmp_path, monkeypatch):
        src = write(tmp_path / "app.infra")
        result = runner.invoke(app, ["up", str(src), "--dry-run", "-t", "helm"])
        assert result.exit_code == 0
        assert "helm upgrade --install" in result.stdout

    def test_up_dry_run_namespace(self, tmp_path, monkeypatch):
        src = write(tmp_path / "app.infra")
        result = runner.invoke(
            app, ["up", str(src), "--dry-run", "-t", "kubernetes", "-n", "prod"]
        )
        assert result.exit_code == 0
        assert "-n" in result.stdout and "prod" in result.stdout


class TestUpErrors:
    def test_up_missing_file(self, tmp_path):
        result = runner.invoke(app, ["up", str(tmp_path / "nope.infra"), "--dry-run"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_up_missing_tool_real_exec(self, tmp_path, monkeypatch):
        # non-dry-run: missing kubectl should error and point at doctor
        monkeypatch.setattr("infra.cli.up_cmd.shutil.which", lambda name: None)
        src = write(tmp_path / "app.infra")
        result = runner.invoke(app, ["up", str(src), "-t", "kubernetes"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()
        assert "infra doctor" in result.stdout

    def test_up_unsupported_target(self, tmp_path):
        src = write(tmp_path / "app.infra")
        result = runner.invoke(app, ["up", str(src), "--dry-run", "-t", "terraform"])
        assert result.exit_code == 1
        assert "unsupported" in result.stdout.lower()

    def test_up_invalid_infra(self, tmp_path):
        src = write(tmp_path / "bad.infra", 'service api { image: }')
        result = runner.invoke(app, ["up", str(src), "--dry-run"])
        assert result.exit_code == 1


class TestDown:
    def test_down_dry_run_compose(self, tmp_path, monkeypatch):
        src = write(tmp_path / "app.infra")
        # down executes; mock the tool to avoid a real docker invocation
        monkeypatch.setattr("infra.cli.up_cmd._have_tool", lambda binary: True)
        monkeypatch.setattr("infra.cli.up_cmd._run", lambda cmd, cwd=None: type(
            "R", (), {"returncode": 0, "stdout": "down", "stderr": ""}
        )())
        result = runner.invoke(app, ["down", str(src), "-t", "compose"])
        assert result.exit_code == 0
        assert "docker compose" in result.stdout
        assert "down -v" in result.stdout

    def test_down_missing_file(self, tmp_path):
        result = runner.invoke(app, ["down", str(tmp_path / "nope.infra")])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_down_helm(self, tmp_path, monkeypatch):
        src = write(tmp_path / "app.infra")
        monkeypatch.setattr("infra.cli.up_cmd._have_tool", lambda binary: True)
        monkeypatch.setattr("infra.cli.up_cmd._run", lambda cmd, cwd=None: type(
            "R", (), {"returncode": 0, "stdout": "uninstalled", "stderr": ""}
        )())
        result = runner.invoke(app, ["down", str(src), "-t", "helm"])
        assert result.exit_code == 0
        assert "helm uninstall" in result.stdout
