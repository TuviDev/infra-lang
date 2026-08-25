"""Tests for the v0.4.5 ``--all`` batch workspace processing.

``infra check|validate|cost|doctor|fmt --all`` recursively discovers every
``.infra`` file under the working directory (skipping hidden and vendor
folders), renders a summary table and supports an aggregate ``--json``
document for CI/CD. Explicit single-file behavior is untouched.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from infra.cli.batch import discover_infra_files
from infra.cli.main import app

runner = CliRunner()

GOOD = (
    "service api {\n"
    '    image: "app:1.0"\n'
    "    depends_on: [db]\n"
    "}\n"
    "\n"
    "database db {\n"
    "    type: postgres\n"
    "}\n"
)
GOOD2 = (
    "service worker {\n"
    '    image: "worker:2.0"\n'
    "    depends_on: [jobs]\n"
    "}\n"
    "\n"
    "queue jobs {\n"
    "    type: rabbitmq\n"
    "}\n"
)
BAD = "service broken {\n    image: \n}\n"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A small workspace: two good files, one broken, plus skippable dirs."""
    (tmp_path / "main.infra").write_text(GOOD)
    sub = tmp_path / "services"
    sub.mkdir()
    (sub / "worker.infra").write_text(GOOD2)
    (tmp_path / "bad.infra").write_text(BAD)
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "h.infra").write_text('service hidden { image: "x" }\n')
    vendor = tmp_path / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "v.infra").write_text('service vendor { image: "x" }\n')
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestDiscovery:
    def test_skips_hidden_and_vendor_dirs(self, workspace):
        found = discover_infra_files(workspace)
        names = sorted(f.relative_to(workspace).as_posix() for f in found)
        assert names == ["bad.infra", "main.infra", "services/worker.infra"]

    def test_sorted_and_deterministic(self, workspace):
        assert discover_infra_files(workspace) == discover_infra_files(workspace)

    def test_empty_workspace(self, tmp_path):
        assert discover_infra_files(tmp_path) == []


class TestCheckAll:
    def test_table_and_summary(self, workspace):
        result = runner.invoke(app, ["check", "--all"])
        assert result.exit_code == 1
        assert "bad.infra" in result.output
        assert "main.infra" in result.output
        assert "Checked 3 files: 2 valid, 1 errors" in result.output

    def test_all_valid(self, workspace):
        (workspace / "bad.infra").unlink()
        result = runner.invoke(app, ["check", "--all"])
        assert result.exit_code == 0
        assert "Checked 2 files: 2 valid, 0 errors" in result.output

    def test_short_flag(self, workspace):
        result = runner.invoke(app, ["check", "-a"])
        assert "Checked 3 files" in result.output

    def test_json_aggregate(self, workspace):
        result = runner.invoke(app, ["check", "--all", "--json"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["command"] == "check"
        assert payload["files"] == 3
        assert payload["valid"] == 2
        assert payload["errors"] == 1
        by_file = {r["file"]: r for r in payload["results"]}
        assert by_file["bad.infra"]["ok"] is False
        assert by_file["main.infra"]["ok"] is True

    def test_max_cost_guardrail(self, workspace):
        (workspace / "bad.infra").unlink()
        (workspace / "main.infra").write_text(
            'service api { image: "x" resources { '
            "requests: { cpu: 500m, memory: 512Mi } } }\n"
        )
        (workspace / "services" / "worker.infra").unlink()
        result = runner.invoke(app, ["check", "--all", "--max-cost", "0.01"])
        assert result.exit_code == 1
        assert "COST_EXCEEDED" in result.output

    def test_empty_workspace(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["check", "--all"])
        assert result.exit_code == 0
        assert "No .infra files found" in result.output


class TestValidateAll:
    def test_table_with_warnings(self, workspace):
        result = runner.invoke(app, ["validate", "--all"])
        assert result.exit_code == 1
        assert "Validated 3 files: 2 valid, 1 errors" in result.output

    def test_json_aggregate_counts_warnings(self, workspace):
        result = runner.invoke(app, ["validate", "--all", "--json"])
        payload = json.loads(result.output)
        assert payload["command"] == "validate"
        assert payload["files"] == 3
        assert payload["warnings"] > 0  # reliability hints on the good files
        assert payload["errors"] == 1

    def test_all_valid_exit_zero(self, workspace):
        (workspace / "bad.infra").unlink()
        result = runner.invoke(app, ["validate", "--all"])
        assert result.exit_code == 0
        assert "Validated 2 files: 2 valid, 0 errors" in result.output


class TestCostAll:
    def test_table_shows_monthly_cost(self, workspace):
        result = runner.invoke(app, ["cost", "--all"])
        assert result.exit_code == 1
        assert "USD/mo" in result.output
        assert "Estimated 3 files: 2 valid, 1 errors" in result.output

    def test_json_has_total(self, workspace):
        result = runner.invoke(app, ["cost", "--all", "--json"])
        payload = json.loads(result.output)
        assert payload["command"] == "cost"
        assert payload["currency"] == "USD"
        assert payload["total_monthly_usd"] > 0
        per_file = {
            r["file"]: r.get("monthly_usd") for r in payload["results"] if r["ok"]
        }
        assert abs(sum(per_file.values()) - payload["total_monthly_usd"]) < 0.01

    def test_output_file_rejected(self, workspace):
        result = runner.invoke(
            app, ["cost", "--all", "--output", "report.md", "--format", "markdown"]
        )
        assert result.exit_code == 1

    def test_currency_conversion_in_table(self, workspace):
        result = runner.invoke(app, ["cost", "--all", "--currency", "PLN"])
        assert "PLN/mo" in result.output


class TestFmtAll:
    def test_rewrites_and_summary(self, workspace):
        messy = workspace / "services" / "worker.infra"
        messy.write_text('service worker { image: "worker:2.0" depends_on: [jobs] }\n')
        (workspace / "bad.infra").unlink()
        result = runner.invoke(app, ["fmt", "--all"])
        assert result.exit_code == 0
        assert "Formatted 2 files" in result.output
        # depends_on survives the round-trip
        assert "depends_on: [jobs]" in messy.read_text()

    def test_check_mode_detects_pending_changes(self, workspace):
        (workspace / "bad.infra").unlink()
        messy = workspace / "services" / "worker.infra"
        messy.write_text("service worker {   image: \"x\"   depends_on: [jobs] }\n")
        result = runner.invoke(app, ["fmt", "--all", "--check"])
        assert result.exit_code == 1
        assert "would reformat" in result.output
        # idempotent afterwards
        runner.invoke(app, ["fmt", "--all"])
        result2 = runner.invoke(app, ["fmt", "--all", "--check"])
        assert result2.exit_code == 0

    def test_parse_error_fails(self, workspace):
        result = runner.invoke(app, ["fmt", "--all"])
        assert result.exit_code == 1
        assert "failed" in result.output


class TestDoctorAll:
    def test_env_checks_and_workspace_table(self, workspace):
        result = runner.invoke(app, ["doctor", "--all"])
        assert result.exit_code == 1  # bad.infra fails
        assert "Infra Lang v" in result.output
        assert "Diagnosed 3 files: 2 valid, 1 errors" in result.output

    def test_json_contains_checks_and_workspace(self, workspace):
        result = runner.invoke(app, ["doctor", "--all", "--json"])
        payload = json.loads(result.output)
        assert "version" in payload
        assert "workspace" in payload
        assert payload["workspace"]["files"] == 3
        assert payload["workspace"]["valid"] == 2

    def test_all_valid_exit_zero(self, workspace):
        (workspace / "bad.infra").unlink()
        result = runner.invoke(app, ["doctor", "--all"])
        assert result.exit_code == 0


class TestUsageAndBackCompat:
    @pytest.mark.parametrize("cmd", ["check", "validate", "cost", "fmt"])
    def test_neither_files_nor_all_is_usage_error(self, cmd):
        result = runner.invoke(app, [cmd])
        assert result.exit_code == 2
        assert "--all" in result.output

    def test_single_file_check_unchanged(self, workspace):
        result = runner.invoke(app, ["check", "main.infra"])
        assert result.exit_code == 0
        assert "[OK] 1 file(s) syntactically valid" in result.output

    def test_single_file_validate_unchanged(self, workspace):
        result = runner.invoke(app, ["validate", "main.infra"])
        assert result.exit_code == 0
        # reliability warnings are reported, but the file is valid
        assert "Found 5 warnings" in result.output

    def test_single_file_fmt_unchanged(self, workspace):
        result = runner.invoke(app, ["fmt", "main.infra"])
        assert result.exit_code == 0
        assert "already formatted" in result.output or "Formatted" in result.output

    def test_single_file_cost_unchanged(self, workspace):
        result = runner.invoke(app, ["cost", "main.infra"])
        assert result.exit_code == 0
        assert "TOTAL" in result.output

    def test_explicit_files_not_filtered(self, workspace):
        # vendor/hidden filtering only applies to --all discovery
        target = workspace / "node_modules" / "pkg" / "v.infra"
        result = runner.invoke(app, ["check", str(target)])
        assert result.exit_code == 0
