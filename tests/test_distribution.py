"""Distribution and packaging tests."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

import subprocess
import sys
from pathlib import Path


class TestPackageStructure:
    def test_version_importable(self):
        import infra

        assert hasattr(infra, "__version__")
        assert infra.__version__ == "0.5.4"

    def test_version_format_valid(self):
        from infra.version import VERSION_INFO, __version__

        parts = __version__.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)
        assert isinstance(VERSION_INFO, tuple)
        assert len(VERSION_INFO) == 3
        assert VERSION_INFO == tuple(int(p) for p in __version__.split("."))

    def test_public_api_complete(self):
        from infra import __version__, parse, validate

        assert callable(parse)
        assert callable(validate)
        assert isinstance(__version__, str)

    def test_top_level_parse_file_wrapper(self, tmp_path):
        # covers src/infra/__init__.py:31-33 (only module below 90% per audit)
        import infra

        p = tmp_path / "app.infra"
        p.write_text('service api { image: "nginx" }', encoding="utf-8")
        prog = infra.parse_file(p)
        assert prog.statements, "expected parsed statements from parse_file"

    def test_top_level_compile_accepts_source_string(self):
        # covers src/infra/__init__.py:67 (compile from raw source text)
        import infra

        result = infra.compile('service api { image: "nginx" }', "kubernetes")
        assert result.files, "compile(source, target) should produce files"

    def test_cli_importable(self):
        from infra.cli.main import app

        assert app is not None

    def test_grammar_file_in_package(self):
        import infra

        pkg = Path(infra.__file__).parent
        grammar = pkg / "lexer" / "grammar.lark"
        assert grammar.exists(), f"grammar.lark not found at {grammar}"
        assert grammar.stat().st_size > 1000, "grammar.lark seems too small"

    def test_prelude_file_in_package(self):
        import infra

        pkg = Path(infra.__file__).parent
        prelude = pkg / "stdlib" / "prelude.infra"
        assert prelude.exists(), f"prelude.infra not found at {prelude}"

    def test_all_backends_importable(self):
        from infra.backends.compose import DockerComposeBackend
        from infra.backends.github import GitHubActionsBackend
        from infra.backends.kubernetes import KubernetesBackend
        from infra.backends.terraform import TerraformBackend

        for cls in [KubernetesBackend, DockerComposeBackend,
                    TerraformBackend, GitHubActionsBackend]:
            assert callable(cls)

    def test_all_analyzers_importable(self):
        from infra.analyzer.reliability import ReliabilityChecker
        from infra.analyzer.security import SecurityChecker
        from infra.analyzer.symbols import SymbolTable
        from infra.analyzer.types import INT, STRING
        from infra.analyzer.validator import SemanticValidator

        assert STRING is not None and INT is not None
        assert callable(SemanticValidator)

    def test_resolver_importable(self):
        from infra.resolver.extends import ExtendsResolver
        from infra.resolver.imports import ImportResolver

        assert callable(ImportResolver)
        assert callable(ExtendsResolver)

    def test_diff_engine_importable(self):
        from infra.diff.engine import InfraDiff

        assert callable(InfraDiff)


@pytest.mark.slow  # spawns a real `python -m infra` per test (~1-3 s each)
@pytest.mark.timeout(300)
class TestCLISubprocess:
    def _run(self, *args, timeout=30):
        return subprocess.run(
            [sys.executable, "-m", "infra"] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_help_exit_0(self):
        r = self._run("--help")
        assert r.returncode == 0
        assert "compile" in r.stdout.lower() or "infra" in r.stdout.lower()

    def test_version_exit_0(self):
        r = self._run("--version")
        assert r.returncode == 0
        assert "0.5.4" in r.stdout

    def test_compile_help_exit_0(self):
        assert self._run("compile", "--help").returncode == 0

    def test_validate_help_exit_0(self):
        assert self._run("validate", "--help").returncode == 0

    def test_compile_valid_file_dry_run(self, tmp_path):
        f = tmp_path / "t.infra"
        f.write_text('service api { image: "nginx:1.25" }')
        r = self._run("compile", str(f), "--dry-run")
        assert r.returncode == 0, f"compile failed:\n{r.stdout}\n{r.stderr}"

    def test_validate_valid_exit_0(self, tmp_path):
        f = tmp_path / "t.infra"
        f.write_text('service api { image: "nginx:1.25" }')
        assert self._run("validate", str(f)).returncode == 0

    def test_validate_invalid_exit_1(self, tmp_path):
        f = tmp_path / "t.infra"
        f.write_text('service api { image: "nginx" replicas: 0 }')
        assert self._run("validate", str(f)).returncode == 1

    def test_compile_nonexistent_file_exit_nonzero(self):
        assert self._run("compile", "nonexistent.infra").returncode != 0

    def test_diff_help_exit_0(self):
        assert self._run("diff", "--help").returncode == 0

    def test_fmt_check_valid_file(self, tmp_path):
        f = tmp_path / "t.infra"
        f.write_text('service api { image: "nginx:1.25" }')
        self._run("fmt", str(f))
        assert self._run("fmt", str(f), "--check").returncode == 0
