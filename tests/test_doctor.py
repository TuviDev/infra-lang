"""Tests for `infra doctor` environment diagnostics."""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

import infra.cli.doctor as doctor_mod
from infra.cli.main import app
from infra.version import __version__

runner = CliRunner()


def _fake_run_factory(stdout: str, returncode: int = 0):
    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0], returncode=returncode, stdout=stdout, stderr=""
        )

    return _run


@pytest.fixture
def no_tools(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: None)
    return None


@pytest.fixture
def all_tools(monkeypatch):
    monkeypatch.setattr(
        doctor_mod.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name != "pygls" else None,
    )
    monkeypatch.setattr(
        doctor_mod.subprocess,
        "run",
        _fake_run_factory("someversion 1.2.3\n"),
    )
    return None


def test_doctor_prints_version():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert f"Infra Lang v{__version__}" in result.stdout


def test_doctor_exit_zero_when_missing_tools(no_tools):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0


def test_doctor_lists_python_version():
    result = runner.invoke(app, ["doctor"])
    assert "Python" in result.stdout
    assert "✓" in result.stdout  # 3.11+ passes


def test_doctor_docker_not_found(no_tools):
    result = runner.invoke(app, ["doctor"])
    assert "Docker: not found ✗" in result.stdout


def test_doctor_docker_running(all_tools):
    result = runner.invoke(app, ["doctor"])
    assert "Docker: running" in result.stdout
    assert "✓" in result.stdout


def test_doctor_kubectl_version_present(all_tools):
    result = runner.invoke(app, ["doctor"])
    assert "kubectl:" in result.stdout
    assert "1.2.3" in result.stdout


def test_doctor_missing_summary(no_tools):
    result = runner.invoke(app, ["doctor"])
    assert "Missing:" in result.stdout
    assert "Docker" in result.stdout


def test_doctor_pygls_installed():
    try:
        import pygls  # noqa: F401

        present = True
    except ImportError:
        present = False
    result = runner.invoke(app, ["doctor"])
    if present:
        assert "LSP (pygls): installed ✓" in result.stdout
    else:
        assert "not installed" in result.stdout
