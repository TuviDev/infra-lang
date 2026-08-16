"""Regression tests for live-E2E tool detection (tests/tools.py).

The critical contract: ``have_docker()`` must report the Docker *daemon*, not
just the CLI binary. On CI runners (especially Windows/macOS) the docker CLI
can be installed while Docker Desktop is not running; live E2E must then
silently skip instead of failing.
"""

from __future__ import annotations

import os
import textwrap

import pytest


def _fake_docker_script(daemon_up: bool) -> str:
    """A fake `docker` script that answers `docker info` like a real daemon."""
    if daemon_up:
        return textwrap.dedent(
            """\
            #!/bin/sh
            echo 'Server Version: 25.0.0'
            exit 0
            """
        )
    return textwrap.dedent(
        """\
        #!/bin/sh
        echo 'error during connect: connection refused' >&2
        exit 1
        """
    )


def _install_fake_docker(tmp_path, daemon_up: bool):
    """Write a fake `docker` onto PATH and return the PATH-prefixed string."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(
        _fake_docker_script(daemon_up), encoding="utf-8"
    )
    (bin_dir / "docker").chmod(0o755)
    return str(bin_dir)


@pytest.fixture(autouse=True)
def _clear_tool_cache():
    # have_docker is not cached, but other helpers may cache `which`; reload to
    # avoid cross-test PATH contamination.
    import importlib

    import tests.tools

    importlib.reload(tests.tools)
    yield
    importlib.reload(tests.tools)


class TestHaveDocker:
    def test_cli_present_but_daemon_down_returns_false(self, tmp_path, monkeypatch):
        bin_dir = _install_fake_docker(tmp_path, daemon_up=False)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

        from tests.tools import have_docker

        assert have_docker() is False

    def test_cli_and_daemon_up_returns_true(self, tmp_path, monkeypatch):
        bin_dir = _install_fake_docker(tmp_path, daemon_up=True)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

        from tests.tools import have_docker

        assert have_docker() is True

    def test_docker_absent_returns_false(self, monkeypatch):
        # Force docker off PATH.
        filtered = [p for p in os.environ["PATH"].split(os.pathsep) if "fake" not in p]
        monkeypatch.setenv("PATH", os.pathsep.join(filtered))
        import shutil

        if shutil.which("docker"):
            pytest.skip("a real docker binary is on PATH; can't force absence")

        from tests.tools import have_docker

        # No docker at all -> False.
        assert have_docker() is False
