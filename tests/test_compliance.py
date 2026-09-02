"""Tests for `infra compliance` — mappings, scanner and CLI (v1.0.0).

Pure offline analysis: no external tools are invoked, all fixtures are
temporary .infra files parsed in-process.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from infra.cli.main import app
from infra.compliance.mappings import (
    CIS_CONTROLS,
    SOC2_CONTROLS,
    STANDARD_TITLES,
    STANDARDS,
    Control,
    controls_for,
)
from infra.compliance.scanner import (
    ComplianceReport,
    ControlResult,
    _evaluate_control,
    scan_file,
    scan_program,
)
from infra.parser import parse_file

runner = CliRunner()

CLEAN = '''service app {
  image: "nginx:1.27"
  port: 8080
  replicas: 2
  expose: true
  health http("/health") {
    interval: 30s
    timeout: 5s
  }
  resources {
    limits: {memory: 512Mi}
  }
  security {
    user: 1000
    read_only_root_filesystem: true
  }
  network_policy {
    allow_from: ["frontend"]
  }
}
'''

DIRTY = '''service root {
  image: "nginx:latest"
  port: 80
  replicas: 1
  expose: true
  env { API_KEY: "hardcoded-secret-value" }
  security {
    user: 0
    privileged: true
  }
}

database db {
  type: postgres
  version: "16"
  storage: 20Gi
  ssl: false
}
'''

NO_SECURITY_BLOCK = '''service app {
  image: "nginx:1.27"
  port: 8080
}
'''

EXPOSED_NO_POLICY = '''service app {
  image: "nginx:1.27"
  port: 8080
  expose: true
}
'''


def _parse(tmp_path: Path, text: str, name: str = "app.infra"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p, parse_file(p)


def _result(report: ComplianceReport, control_id: str) -> ControlResult:
    for result in report.results:
        if result.control.control_id == control_id:
            return result
    raise AssertionError(f"control {control_id} not in report")


def _flat(text: str) -> str:
    clean = re.sub(r"\x1b\[[0-9;]*[mGKH]", "", text)
    return re.sub(r"\s+", " ", clean)


# --------------------------------------------------------------------------- #
# Mappings
# --------------------------------------------------------------------------- #


class TestMappings:
    def test_standards(self):
        assert STANDARDS == ("soc2", "cis", "all")

    def test_controls_for_soc2(self):
        ids = [c.control_id for c in controls_for("soc2")]
        assert ids == ["CC6.1", "CC6.3", "CC7.1", "CC7.2", "A1.1"]

    def test_controls_for_cis(self):
        ids = [c.control_id for c in controls_for("cis")]
        assert ids == ["5.1.1", "5.2.1", "5.2.4", "5.2.5", "5.7.3"]

    def test_controls_for_all(self):
        all_controls = controls_for("all")
        assert all_controls == SOC2_CONTROLS + CIS_CONTROLS

    def test_controls_for_unknown(self):
        with pytest.raises(ValueError, match="Unknown standard"):
            controls_for("pci-dss")

    def test_soc2_mapping_table(self):
        mapping = {c.control_id: c.codes for c in SOC2_CONTROLS}
        assert mapping["CC6.1"] == ("SEC001", "SEC004")
        assert mapping["CC6.3"] == ("SEC003",)
        assert mapping["CC7.1"] == ("SEC003", "SEC006")
        assert mapping["CC7.2"] == ("REL004",)
        assert mapping["A1.1"] == ("REL001", "REL002", "REL003")

    def test_cis_mapping_table(self):
        mapping = {c.control_id: c.codes for c in CIS_CONTROLS}
        assert mapping["5.1.1"] == ("SEC005",)
        assert mapping["5.2.1"] == ("SEC004",)
        assert mapping["5.2.4"] == ()
        assert mapping["5.2.5"] == ("SEC004",)
        assert mapping["5.7.3"] == ()

    def test_standard_titles(self):
        assert STANDARD_TITLES["soc2"].startswith("SOC 2")
        assert "CIS" in STANDARD_TITLES["cis"]
        assert "SOC 2" in STANDARD_TITLES["all"]

    def test_every_control_has_recommendation(self):
        for control in controls_for("all"):
            assert control.recommendation, control.control_id
            assert control.standard in ("soc2", "cis")


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #


class TestScanner:
    def test_clean_program_passes_everything(self, tmp_path):
        p, program = _parse(tmp_path, CLEAN)
        report = scan_program(program, str(p), "all")
        assert report.failed == 0
        assert report.passed == report.total == 10
        assert report.score == 100.0
        assert report.violations == ()

    def test_dirty_program_violates_every_control(self, tmp_path):
        p, program = _parse(tmp_path, DIRTY)
        report = scan_program(program, str(p), "all")
        assert report.passed == 0
        assert report.score == 0.0
        codes = {v.code for v in report.violations}
        assert {"SEC001", "SEC003", "SEC004", "SEC005", "SEC006",
                "REL003", "REL004", "5.2.4", "5.7.3"} <= codes

    def test_soc2_only_controls(self, tmp_path):
        p, program = _parse(tmp_path, DIRTY)
        report = scan_program(program, str(p), "soc2")
        assert report.total == 5
        assert {r.control.standard for r in report.results} == {"soc2"}

    def test_cis_only_controls(self, tmp_path):
        p, program = _parse(tmp_path, DIRTY)
        report = scan_program(program, str(p), "cis")
        assert report.total == 5
        assert {r.control.standard for r in report.results} == {"cis"}

    def test_partial_score(self, tmp_path):
        # EXPOSED_NO_POLICY: only 5.2.4 + 5.7.3 fail (plus REL003/REL004
        # mapped soc2 controls) — compute the exact score.
        p, program = _parse(tmp_path, EXPOSED_NO_POLICY)
        report = scan_program(program, str(p), "cis")
        failed_ids = [r.control.control_id for r in report.results
                      if not r.passed]
        assert sorted(failed_ids) == ["5.2.4", "5.7.3"]
        assert report.score == 60.0

    def test_read_only_root_fs_missing_security_block(self, tmp_path):
        p, program = _parse(tmp_path, NO_SECURITY_BLOCK)
        report = scan_program(program, str(p), "cis")
        result = _result(report, "5.2.4")
        assert not result.passed
        violation = result.violations[0]
        assert violation.resource == "app"
        assert violation.code == "5.2.4"
        assert "read-only" in violation.message
        assert violation.location.endswith(":1:1")

    def test_read_only_root_fs_false(self, tmp_path):
        text = '''service app {
  image: "nginx:1.27"
  security { read_only_root_filesystem: false }
}
'''
        p, program = _parse(tmp_path, text)
        report = scan_program(program, str(p), "cis")
        assert not _result(report, "5.2.4").passed

    def test_network_policy_only_for_public(self, tmp_path):
        # a non-exposed service without network_policy → 5.7.3 passes
        p, program = _parse(tmp_path, NO_SECURITY_BLOCK)
        report = scan_program(program, str(p), "cis")
        assert _result(report, "5.7.3").passed

    def test_network_policy_public_violation_details(self, tmp_path):
        p, program = _parse(tmp_path, EXPOSED_NO_POLICY)
        report = scan_program(program, str(p), "cis")
        result = _result(report, "5.7.3")
        violation = result.violations[0]
        assert violation.resource == "app"
        assert "network_policy" in violation.recommendation

    def test_finding_violation_uses_hint_and_location(self, tmp_path):
        p, program = _parse(tmp_path, DIRTY)
        report = scan_program(program, str(p), "cis")
        violation = _result(report, "5.2.1").violations[0]
        assert violation.code == "SEC004"
        assert "privileged" in violation.recommendation
        assert violation.location.endswith("app.infra:1:1")
        assert violation.resource is None

    def test_finding_hint_falls_back_to_control_fix(self):
        control = Control(
            standard="soc2", control_id="CC9.9", title="x",
            codes=("SEC099",), recommendation="control-level fix",
        )
        finding = SimpleNamespace(
            code="SEC099", message="boom", location=None, hint=None
        )
        result = _evaluate_control(control, [finding], [])
        violation = result.violations[0]
        assert violation.recommendation == "control-level fix"
        assert violation.location == "unknown"

    def test_unknown_detector_raises(self):
        control = Control(
            standard="x", control_id="X0", title="x", detector="bogus"
        )
        with pytest.raises(ValueError, match="Unknown detector"):
            _evaluate_control(control, [], [])

    def test_scan_file_roundtrip(self, tmp_path):
        p, _ = _parse(tmp_path, CLEAN)
        report = scan_file(p, "soc2")
        assert report.file == str(p)
        assert report.score == 100.0

    def test_scan_file_parse_error(self, tmp_path):
        p = tmp_path / "broken.infra"
        p.write_text("service {{{", encoding="utf-8")
        with pytest.raises(Exception):
            scan_file(p, "all")

    def test_empty_report_scores_100(self):
        report = ComplianceReport(file="x", standard="soc2", results=())
        assert report.total == 0
        assert report.failed == 0
        assert report.score == 100.0

    def test_to_dict_structure(self, tmp_path):
        p, program = _parse(tmp_path, DIRTY)
        report = scan_program(program, str(p), "soc2")
        data = report.to_dict()
        assert data["standard"] == "soc2"
        assert data["controls_total"] == 5
        assert data["controls_passed"] == 0
        assert data["controls_failed"] == 5
        first = data["results"][0]
        assert first["control_id"] == "CC6.1"
        assert first["codes"] == ["SEC001", "SEC004"]
        assert first["passed"] is False
        violation = first["violations"][0]
        assert set(violation) == {
            "control_id", "code", "message", "location",
            "resource", "recommendation",
        }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class TestCli:
    def _write(self, tmp_path: Path, text: str, name: str = "app.infra") -> Path:
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_clean_file_exit_0(self, tmp_path):
        p = self._write(tmp_path, CLEAN)
        result = runner.invoke(app, ["compliance", str(p)])
        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        assert "[PASS] CC6.1" in flat
        assert "[PASS] 5.7.3" in flat
        assert "Compliance score: 100.0% (10/10 controls passed)" in flat

    def test_dirty_file_exit_1_with_details(self, tmp_path):
        p = self._write(tmp_path, DIRTY)
        result = runner.invoke(app, ["compliance", str(p)])
        assert result.exit_code == 1
        flat = _flat(result.output)
        assert "[FAIL] CC6.1" in flat
        assert "[SEC004]" in flat
        assert "fix:" in flat
        assert "Compliance score: 0.0%" in flat
        assert "3 violations" not in flat  # sane singular/plural rendering
        assert "(2 violations)" in flat
        assert "(1 violation)" in flat

    def test_standard_soc2(self, tmp_path):
        p = self._write(tmp_path, DIRTY)
        result = runner.invoke(app, ["compliance", str(p), "-s", "soc2"])
        assert result.exit_code == 1
        flat = _flat(result.output)
        assert "CC7.2" in flat
        assert "5.2.4" not in flat
        assert "(0/5 controls passed)" in flat

    def test_standard_cis(self, tmp_path):
        p = self._write(tmp_path, DIRTY)
        result = runner.invoke(app, ["compliance", str(p), "-s", "cis"])
        assert result.exit_code == 1
        flat = _flat(result.output)
        assert "5.2.4" in flat
        assert "CC7.2" not in flat

    def test_standard_case_insensitive(self, tmp_path):
        p = self._write(tmp_path, CLEAN)
        result = runner.invoke(app, ["compliance", str(p), "-s", "SOC2"])
        assert result.exit_code == 0, result.output

    def test_invalid_standard(self, tmp_path):
        p = self._write(tmp_path, CLEAN)
        result = runner.invoke(
            app, ["compliance", str(p), "--standard", "pci"]
        )
        assert result.exit_code == 1
        assert "Unknown standard" in _flat(result.output)

    def test_invalid_format(self, tmp_path):
        p = self._write(tmp_path, CLEAN)
        result = runner.invoke(app, ["compliance", str(p), "-f", "yaml"])
        assert result.exit_code == 1
        assert "Unknown format" in _flat(result.output)

    def test_missing_file(self, tmp_path):
        result = runner.invoke(
            app, ["compliance", str(tmp_path / "gone.infra")]
        )
        assert result.exit_code == 1
        assert "not found" in _flat(result.output)

    def test_parse_error(self, tmp_path):
        p = self._write(tmp_path, "service {{{")
        result = runner.invoke(app, ["compliance", str(p)])
        assert result.exit_code == 1
        assert "Cannot parse" in _flat(result.output)

    def test_json_format(self, tmp_path):
        p = self._write(tmp_path, DIRTY)
        result = runner.invoke(
            app, ["compliance", str(p), "-f", "json", "-s", "soc2"]
        )
        assert result.exit_code == 1
        # stdout must be pure JSON (status goes to stderr)
        data = json.loads(result.output)
        assert data["standard"] == "soc2"
        assert data["score"] == 0.0
        assert len(data["results"]) == 5

    def test_markdown_format(self, tmp_path):
        p = self._write(tmp_path, DIRTY)
        result = runner.invoke(
            app, ["compliance", str(p), "-f", "markdown"]
        )
        assert result.exit_code == 1
        out = result.output
        assert out.startswith("# Compliance Report")
        assert "| Control | Title | Status | Violations |" in out
        assert "## Violations" in out
        assert "### CC6.1" in out
        assert "**[SEC001]**" in out
        assert "- **Compliance score:** 0.0%" in out

    def test_markdown_clean_has_no_violations_section(self, tmp_path):
        p = self._write(tmp_path, CLEAN)
        result = runner.invoke(
            app, ["compliance", str(p), "-f", "markdown"]
        )
        assert result.exit_code == 0, result.output
        assert "## Violations" not in result.output
        assert result.output.endswith("\n")

    def test_output_file(self, tmp_path):
        p = self._write(tmp_path, DIRTY)
        out = tmp_path / "reports" / "soc2.md"
        result = runner.invoke(
            app,
            ["compliance", str(p), "-f", "markdown", "-o", str(out)],
        )
        # violations → exit 1, but the file is still written first
        assert result.exit_code == 1
        assert out.is_file()
        assert out.read_text(encoding="utf-8").startswith(
            "# Compliance Report"
        )
        assert "[OK] compliance report written" in result.output

    def test_output_file_clean_passes(self, tmp_path):
        p = self._write(tmp_path, CLEAN)
        out = tmp_path / "report.txt"
        result = runner.invoke(app, ["compliance", str(p), "-o", str(out)])
        assert result.exit_code == 0, result.output
        content = out.read_text(encoding="utf-8")
        assert "Compliance score: 100.0%" in content

    def test_renderers_handle_empty_recommendation(self):
        from infra.cli.compliance_cmd import _render_markdown, _render_text
        from infra.compliance.scanner import ComplianceViolation

        empty_fix = ComplianceViolation(
            control_id="CC1.1",
            code="SEC999",
            message="synthetic violation",
            location="app.infra:1:1",
            resource=None,
            recommendation="",
        )
        control = Control(
            standard="soc2", control_id="CC1.1", title="Synthetic",
            codes=("SEC999",),
        )
        report = ComplianceReport(
            file="app.infra",
            standard="soc2",
            results=(ControlResult(control=control, violations=(empty_fix,)),),
        )
        text = _render_text(report)
        assert "[FAIL] CC1.1 Synthetic (1 violation)" in text
        assert "fix:" not in text
        markdown = _render_markdown(report)
        assert "### CC1.1 — Synthetic" in markdown
        assert "Fix:" not in markdown

    def test_help_robust(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "120")
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("FORCE_COLOR", "0")
        result = runner.invoke(app, ["compliance", "--help"])
        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        for word in ("--standard", "--format", "--output", "soc2"):
            assert word in flat
