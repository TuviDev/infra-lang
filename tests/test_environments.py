"""Tests for `environment "name" { ... }` overlays and the -e/--env CLI flag."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from infra.analyzer.environments import (
    EnvironmentNotFoundError,
    apply_environment_overlay,
    available_environments,
)
from infra.cli.main import app
from infra.parser import ast_nodes as n
from infra.parser import parse

runner = CliRunner()

BASE = """\
service web {
  image: "nginx"
  replicas: 1
  env { LOG_LEVEL: "debug" }
  labels: { tier: "base" }
}
"""

OVERLAY_SRC = (
    BASE
    + """\
environment "prod" {
  service web {
    replicas: 5
    env { LOG_LEVEL: "info" }
    labels: { tier: "prod" }
  }
}
environment "staging" {
  service web {
    replicas: 2
  }
}
"""
)


def _write_infra(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def _deployment_replicas(out_dir: Path, expected: int) -> dict:
    """Return the deployment manifest after asserting its replica count."""
    for yml in out_dir.glob("*.yaml"):
        data = yaml.safe_load(yml.read_text())
        if data.get("kind") == "Deployment":
            assert data["spec"]["replicas"] == expected
            return data
    raise AssertionError(f"no Deployment manifest found in {out_dir}")


# --------------------------------------------------------------------------- #
# Parser / AST
# --------------------------------------------------------------------------- #


class TestParser:
    def test_parses_environment_overlay_blocks(self):
        prog = parse(OVERLAY_SRC)
        names = available_environments(prog)
        assert names == ("prod", "staging")

    def test_environment_is_separate_from_statements(self):
        prog = parse(OVERLAY_SRC)
        # overlay specs live in program.environments, not statements
        assert all(not isinstance(s, n.EnvironmentSpec) for s in prog.statements)
        assert len(prog.environments) == 2

    def test_overlay_fields_are_coerced(self):
        prog = parse(OVERLAY_SRC)
        spec = prog.environments[0]
        assert spec.name == "prod"
        overlay = spec.overrides[0]
        assert overlay.name == "web"
        assert overlay.replicas == 5
        assert [e.name for e in overlay.env] == ["LOG_LEVEL"]
        assert overlay.labels == (("tier", "prod"),)

    def test_string_name_does_not_collide_with_cluster_environment(self):
        # cluster-style `environment dev { namespace: "ns" }` must still parse
        prog = parse(
            'environment dev { namespace: "ns" }\n'
            'environment "x" { service a { replicas: 3 } }'
        )
        assert any(isinstance(s, n.EnvironmentDef) for s in prog.statements)
        assert available_environments(prog) == ("x",)


# --------------------------------------------------------------------------- #
# Overlay logic
# --------------------------------------------------------------------------- #


class TestApplyOverlay:
    def test_overrides_replicas(self):
        prog = parse(OVERLAY_SRC)
        out = apply_environment_overlay(prog, "prod")
        web = next(s for s in out.statements if getattr(s, "name", "") == "web")
        assert web.replicas == 5

    def test_merges_env_and_labels_overlay_wins(self):
        prog = parse(OVERLAY_SRC)
        out = apply_environment_overlay(prog, "prod")
        web = next(s for s in out.statements if getattr(s, "name", "") == "web")
        env = {e.name: e.value for e in web.env}
        assert env["LOG_LEVEL"].value == "info"  # type: ignore[attr-defined]
        assert dict(web.labels)["tier"] == "prod"

    def test_unselected_env_leaves_base_intact(self):
        prog = parse(OVERLAY_SRC)
        out = apply_environment_overlay(prog, "staging")
        web = next(s for s in out.statements if getattr(s, "name", "") == "web")
        assert web.replicas == 2

    def test_unknown_environment_raises(self):
        prog = parse(OVERLAY_SRC)
        with pytest.raises(EnvironmentNotFoundError):
            apply_environment_overlay(prog, "nope")

    def test_no_environment_blocks_raises_with_hint(self):
        prog = parse('service web { image: "nginx" }')
        with pytest.raises(EnvironmentNotFoundError) as exc:
            apply_environment_overlay(prog, "prod")
        assert "not defined" in str(exc.value)

    def test_overlay_stripped_from_output(self):
        prog = parse(OVERLAY_SRC)
        out = apply_environment_overlay(prog, "prod")
        assert out.environments == ()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class TestCliEnv:
    def test_compile_env_applies_overlay(self, tmp_path):
        src = _write_infra(tmp_path / "app.infra", OVERLAY_SRC)
        out = tmp_path / "out"
        result = runner.invoke(
            app,
            [
                "compile",
                str(src),
                "--target",
                "kubernetes",
                "-e",
                "prod",
                "--output",
                str(out),
                "--split",
            ],
        )
        assert result.exit_code == 0, result.output
        _deployment_replicas(out, expected=5)

    def test_compile_default_is_base(self, tmp_path):
        src = _write_infra(tmp_path / "app.infra", OVERLAY_SRC)
        out = tmp_path / "out"
        result = runner.invoke(
            app,
            [
                "compile",
                str(src),
                "--target",
                "kubernetes",
                "--output",
                str(out),
                "--split",
            ],
        )
        assert result.exit_code == 0, result.output
        _deployment_replicas(out, expected=1)

    def test_compile_unknown_env_fails(self, tmp_path):
        src = _write_infra(tmp_path / "app.infra", OVERLAY_SRC)
        result = runner.invoke(
            app, ["compile", str(src), "--target", "kubernetes", "-e", "nope"]
        )
        assert result.exit_code != 0
        assert "not defined" in result.output

    def test_env_flag_aliases(self, tmp_path):
        src = _write_infra(tmp_path / "app.infra", OVERLAY_SRC)
        for flag in ("-e", "--env", "--environment"):
            out = tmp_path / f"out-{flag.lstrip('-')}"
            result = runner.invoke(
                app,
                [
                    "compile",
                    str(src),
                    "--target",
                    "kubernetes",
                    flag,
                    "prod",
                    "--output",
                    str(out),
                    "--split",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_validate_env(self, tmp_path):
        src = _write_infra(tmp_path / "app.infra", OVERLAY_SRC)
        result = runner.invoke(app, ["validate", str(src), "-e", "prod"])
        assert result.exit_code == 0, result.output

    def test_validate_unknown_env_reports_env_error(self, tmp_path):
        src = _write_infra(tmp_path / "app.infra", OVERLAY_SRC)
        result = runner.invoke(app, ["validate", str(src), "-e", "nope"])
        assert result.exit_code != 0
        assert "not defined" in result.output

    def test_cost_env_reflects_replicas(self, tmp_path):
        src = _write_infra(tmp_path / "app.infra", OVERLAY_SRC)
        base = runner.invoke(app, ["cost", str(src), "--json"])
        prod = runner.invoke(app, ["cost", str(src), "-e", "prod", "--json"])
        assert base.exit_code == 0 and prod.exit_code == 0
        base_total = base.stdout.strip()
        prod_total = prod.stdout.strip()
        # prod has 5x replicas, so its estimate must be larger than base
        import json as _json

        base_n = _json.loads(base_total)["total_monthly_usd"]
        prod_n = _json.loads(prod_total)["total_monthly_usd"]
        assert prod_n > base_n

    def test_up_dry_run_env_flag_accepted(self, tmp_path):
        src = _write_infra(tmp_path / "app.infra", OVERLAY_SRC)
        result = runner.invoke(
            app,
            ["up", str(src), "--target", "kubernetes", "--dry-run", "-e", "prod"],
        )
        # dry-run doesn't require kubectl; should print commands and succeed
        assert result.exit_code == 0, result.output
        assert "kubectl apply" in result.output
