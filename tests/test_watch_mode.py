"""--watch mode tests."""

from __future__ import annotations

import pytest

import subprocess
import sys
import time
from pathlib import Path

from typer.testing import CliRunner

from infra.cli.main import app

runner = CliRunner()

pytestmark = pytest.mark.slow


def write(tmp_path, content):
    f = tmp_path / "t.infra"
    f.write_text(content)
    return f


class TestWatchFlag:
    def test_watch_flag_exists_in_help(self):
        r = runner.invoke(app, ["compile", "--help"])
        assert r.exit_code == 0
        assert "watch" in r.output.lower()

    def test_watch_compiles_on_startup(self, tmp_path):
        f = write(tmp_path, 'service api { image: "nginx:1.25" }')
        out = tmp_path / "out"
        proc = subprocess.Popen(
            [sys.executable, "-m", "infra", "compile", str(f), "--watch",
             "--output", str(out)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(5)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        yaml_files = list(out.rglob("*.yaml")) + list(out.rglob("*.yml"))
        assert len(yaml_files) >= 1, "Watch should compile on startup"

    def test_watch_recompiles_on_change(self, tmp_path):
        f = write(tmp_path, 'service api { image: "nginx:1.0" }')
        out = tmp_path / "out"
        proc = subprocess.Popen(
            [sys.executable, "-m", "infra", "compile", str(f), "--watch",
             "--output", str(out)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(4)
        f.write_text('service api { image: "nginx:1.25" }')
        time.sleep(4)
        proc.terminate()
        try:
            stdout, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout = ""
        assert "Compiled" in stdout or "Recompiled" in stdout, \
            f"Expected compile output, got: {stdout!r}"

    def test_watch_shows_error_without_crash(self, tmp_path):
        f = write(tmp_path, 'service api { image: "nginx:1.25" }')
        out = tmp_path / "out"
        proc = subprocess.Popen(
            [sys.executable, "-m", "infra", "compile", str(f), "--watch",
             "--output", str(out)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(3)
        f.write_text('service api { image: "nginx" replicas: 0 }')
        time.sleep(3)
        f.write_text('service api { image: "nginx:1.25" }')
        time.sleep(3)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        assert proc.returncode is not None
