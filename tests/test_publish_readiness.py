"""Tests that verify the package is ready for PyPI.

These run locally before any publish attempt. See MANUAL_PUBLISH_STEPS.md.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


# Packaging integration (wheel/sdist build, twine, clean-venv install) is
# gated separately by CI steps; mark slow so the Windows smoke profile
# (`-m "not slow"`) can skip it without losing the Linux gate.
@pytest.mark.slow
@pytest.mark.timeout(300)
class TestPublishReadiness:
    def test_wheel_exists(self):
        wheels = list(Path("dist").glob("*.whl"))
        assert len(wheels) >= 1, "No wheel found. Run: python -m build"

    def test_sdist_exists(self):
        sdists = list(Path("dist").glob("*.tar.gz"))
        assert len(sdists) >= 1, "No sdist found. Run: python -m build"

    def test_twine_check_passes(self):
        result = subprocess.run(
            [sys.executable, "-m", "twine", "check", "dist/*"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"twine check failed:\n{result.stdout}\n{result.stderr}"
        )

    def test_wheel_contains_grammar(self):
        wheels = list(Path("dist").glob("*.whl"))
        assert wheels
        with zipfile.ZipFile(wheels[0]) as z:
            names = z.namelist()
            assert any("grammar.lark" in n for n in names), (
                "grammar.lark missing from wheel"
            )

    def test_wheel_contains_prelude(self):
        wheels = list(Path("dist").glob("*.whl"))
        with zipfile.ZipFile(wheels[0]) as z:
            names = z.namelist()
            assert any("prelude.infra" in n for n in names), (
                "prelude.infra missing from wheel"
            )

    def test_package_version_consistent(self):
        from infra.version import __version__

        content = Path("pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'version\s*=\s*"([^"]+)"', content)
        assert m, "pyproject.toml has no version"
        assert __version__ == m.group(1), (
            f"version.py: {__version__!r} != pyproject.toml: {m.group(1)!r}"
        )

    def test_cli_entry_point_works(self):
        result = subprocess.run(
            [sys.executable, "-m", "infra", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

    def test_runtime_dependencies_declared(self):
        """Every runtime module import must have a declared dependency.

        This guards against a clean-venv install where an undeclared dependency
        (e.g. PyYAML) would break features like --validate-output and config.
        """
        content = Path("pyproject.toml").read_text(encoding="utf-8")
        deps = content.lower()
        # the runtime modules import these packages directly
        required = {
            "lark": "lark",
            "typer": "typer",
            "rich": "rich",
            "ruamel": "ruamel.yaml",
            "yaml": "pyyaml",
            "watchdog": "watchdog",
            "prompt_toolkit": "prompt_toolkit",
        }
        for mod, pkg in required.items():
            assert pkg in deps, f"missing runtime dependency: {pkg} (used by {mod})"

    def test_clean_venv_install(self, tmp_path):
        venv = tmp_path / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            check=True,
            timeout=60,
        )
        # venv scripts live under bin/ on POSIX and Scripts/ on Windows
        bindir = venv / ("Scripts" if os.name == "nt" else "bin")
        pip = bindir / ("pip.exe" if os.name == "nt" else "pip")
        infra = bindir / ("infra.exe" if os.name == "nt" else "infra")
        wheels = list(Path("dist").glob("*.whl"))
        assert wheels
        subprocess.run(
            [str(pip), "install", str(wheels[0]), "-q"],
            check=True,
            timeout=120,
        )
        result = subprocess.run(
            [str(infra), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert __version__ in result.stdout or "infra" in result.stdout.lower() or "infra" in result.stdout.lower()
