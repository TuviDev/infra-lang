"""Regression tests for live-E2E tool detection (tests/tools.py).

The critical contract: ``have_docker()`` must report the Docker *daemon*, not
just the CLI binary. On CI runners (especially Windows/macOS) the docker CLI
can be installed while Docker Desktop is not running; live E2E must then
silently skip instead of failing.

We mock ``subprocess.run`` / ``shutil.which`` directly (rather than creating a
fake docker binary), so the tests are cross-platform — no shell-script
gymnastics that would break on Windows.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest


def _fake_run(returncode: int, *, daemon_up: bool = True):
    """Build a ``subprocess.run`` stand-in for the daemon probe."""

    def run(cmd, **kwargs):
        # have_docker() runs `docker info`; return code mirrors daemon state.
        result = Mock()
        result.returncode = returncode
        return result

    return run


def _patch_docker_which(monkeypatch, present: bool = True):
    """Mock ``shutil.which`` so docker is (or is not) found on PATH."""

    def which(name):
        return "/usr/bin/docker" if (present and name == "docker") else None

    monkeypatch.setattr("shutil.which", which)


@pytest.fixture(autouse=True)
def _clear_tool_cache():
    # Reload so no earlier `shutil.which` / `subprocess.run` patch leaks across
    # tests in the module.
    import importlib

    import tests.tools

    importlib.reload(tests.tools)
    yield
    importlib.reload(tests.tools)


class TestHaveDocker:
    def test_cli_present_but_daemon_down_returns_false(self, monkeypatch):
        _patch_docker_which(monkeypatch, present=True)
        monkeypatch.setattr("subprocess.run", _fake_run(returncode=1))

        from tests.tools import have_docker

        assert have_docker() is False

    def test_cli_and_daemon_up_returns_true(self, monkeypatch):
        _patch_docker_which(monkeypatch, present=True)
        monkeypatch.setattr("subprocess.run", _fake_run(returncode=0))

        from tests.tools import have_docker

        assert have_docker() is True

    def test_docker_absent_returns_false(self, monkeypatch):
        _patch_docker_which(monkeypatch, present=False)
        # subprocess.run would not be called; keep it returning failure just in
        # case the implementation changes.
        monkeypatch.setattr("subprocess.run", _fake_run(returncode=0))

        from tests.tools import have_docker

        assert have_docker() is False

    def test_docker_probe_times_out_returns_false(self, monkeypatch):
        # `docker info` hanging must not block forever -> False.
        _patch_docker_which(monkeypatch, present=True)

        def hang(cmd, **kwargs):
            raise TimeoutError("timeout")

        monkeypatch.setattr("subprocess.run", hang)

        from tests.tools import have_docker

        assert have_docker() is False

    def test_docker_probe_oserror_returns_false(self, monkeypatch):
        _patch_docker_which(monkeypatch, present=True)

        def boom(cmd, **kwargs):
            raise OSError("not found")

        monkeypatch.setattr("subprocess.run", boom)

        from tests.tools import have_docker

        assert have_docker() is False
