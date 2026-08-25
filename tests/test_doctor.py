"""Tests for `infra doctor` environment diagnostics."""

from __future__ import annotations

import json
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
    assert "[OK]" in result.stdout  # 3.11+ passes


def test_doctor_docker_not_found(no_tools):
    result = runner.invoke(app, ["doctor"])
    assert "Docker: not found [FAIL]" in result.stdout


def test_doctor_docker_running(all_tools):
    result = runner.invoke(app, ["doctor"])
    assert "Docker: running" in result.stdout
    assert "[OK]" in result.stdout


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
        assert "LSP (pygls): installed [OK]" in result.stdout
    else:
        assert "not installed" in result.stdout


def test_doctor_json_output():
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "python" in data
    assert "docker" in data
    assert "kubectl" in data
    assert data["python"]["installed"] is True  # sandbox runs Python 3.11+
    assert "version" in data


def test_doctor_json_parseable(no_tools):
    result = runner.invoke(app, ["doctor", "--json"])
    data = json.loads(result.stdout)
    assert data["docker"]["installed"] is False


def test_doctor_check_drift_json_clean(tmp_path):
    from infra.parser import parse_file
    from infra.backends import get_backend

    src = tmp_path / "app.infra"
    src.write_text('service api { image: "nginx:1.25" port 80 }', encoding="utf-8")
    out = tmp_path / "out"
    prog = parse_file(src)
    for name, content in get_backend("kubernetes").compile(prog).files.items():
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    result = runner.invoke(
        app, ["doctor", "--check-drift", str(src), "--out-dir", str(out), "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["has_drift"] is False
    assert data["missing_files"] == []


def test_doctor_check_drift_json_modified(tmp_path):
    from infra.parser import parse_file
    from infra.backends import get_backend

    src = tmp_path / "app.infra"
    src.write_text('service api { image: "nginx:1.25" port 80 }', encoding="utf-8")
    out = tmp_path / "out"
    prog = parse_file(src)
    for name, content in get_backend("kubernetes").compile(prog).files.items():
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    target = out / "infra.yaml"
    target.write_text(
        target.read_text(encoding="utf-8") + "  # drifted\n", encoding="utf-8"
    )
    result = runner.invoke(
        app, ["doctor", "--check-drift", str(src), "--out-dir", str(out), "--json"]
    )
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["has_drift"] is True
    assert any(m["path"] == "infra.yaml" for m in data["modified_files"])
