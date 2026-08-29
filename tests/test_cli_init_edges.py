"""Edge-case tests for the ``infra init`` command (v0.5.3).

Covers the interactive prompt path, the ``--yes`` name fallback and the
``git init`` subprocess branch.
"""

from __future__ import annotations

import os
import shutil

import pytest
from typer.testing import CliRunner

from infra.cli.main import app

runner = CliRunner()


@pytest.fixture
def workdir(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)


class TestInteractivePrompts:
    def test_prompts_for_name_and_target(self, workdir) -> None:
        result = runner.invoke(app, ["init", "--no-git"], input="myproj\ncompose\n")
        assert result.exit_code == 0, result.output
        cfg = workdir / "myproj" / ".infra-config.yaml"
        assert cfg.exists()
        assert "default_target: compose" in cfg.read_text(encoding="utf-8")

    def test_yes_without_name_falls_back_to_default(self, workdir) -> None:
        result = runner.invoke(app, ["init", "--yes", "--no-git"])
        assert result.exit_code == 0, result.output
        assert (workdir / "my-project" / ".infra-config.yaml").exists()


class TestGitInitBranch:
    @pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
    def test_git_repo_initialized_when_git_available(self, workdir) -> None:
        result = runner.invoke(app, ["init", "gitproj", "--yes"])
        assert result.exit_code == 0, result.output
        assert (workdir / "gitproj" / ".git").is_dir()

    def test_git_failure_is_silently_ignored(self, workdir) -> None:
        # An empty PATH makes the git subprocess fail; init must still succeed.
        env = dict(os.environ)
        env["PATH"] = ""
        result = runner.invoke(app, ["init", "nogitproj", "--yes"], env=env)
        assert result.exit_code == 0, result.output
        assert (workdir / "nogitproj" / ".infra-config.yaml").exists()
        assert not (workdir / "nogitproj" / ".git").exists()
