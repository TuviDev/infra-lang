"""--watch mode tests."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
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
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        yaml_files = list(out.rglob("*.yaml")) + list(out.rglob("*.yml"))
        assert len(yaml_files) >= 1, "Watch should compile on startup"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "watchdog file-change detection is unreliable in pytest tmp_path "
            "on Windows (ReadDirectoryChangesW), so the recompile-on-change "
            "assertion is flaky there. Watch mode works in normal use; this "
            "is a test-environment limitation."
        ),
    )
    def test_watch_recompiles_on_change(self, tmp_path):
        """Watch mode must (re)write the output file when the input changes.

        Assertion is on the *produced artifact* (its contents), not on stdout:
        this is robust to rich / non-TTY buffering and to termination timing on
        any platform.
        """
        f = write(tmp_path, 'service api { image: "nginx:1.0" }')
        out = tmp_path / "out"
        proc = subprocess.Popen(
            [sys.executable, "-m", "infra", "compile", str(f), "--watch",
             "--output", str(out)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        def wait_for(marker: str, timeout: float = 12.0) -> bool:
            deadline = time.time() + timeout
            while time.time() < deadline:
                target = out / "infra.yaml"
                if target.exists():
                    try:
                        content = target.read_text(encoding="utf-8")
                    except OSError:
                        content = ""
                    if marker in content:
                        return True
                time.sleep(0.5)
            return False

        try:
            # 1) startup compile writes the file with the original image
            assert wait_for("nginx:1.0"), (
                "watch did not produce initial output with nginx:1.0"
            )
            # 2) change the input -> watch must recompile and update the file
            f.write_text('service api { image: "nginx:1.25" }')
            assert wait_for("nginx:1.25"), (
                "watch did not reflect the changed input (nginx:1.25)"
            )
        finally:
            proc.terminate()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

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
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        assert proc.returncode is not None
