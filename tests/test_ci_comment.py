import re

"""Tests for `infra ci-comment` — PR report generator (v0.7.0)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from infra.cli.ci_comment import (
    COMMENT_MARKER,
    CiReport,
    build_report,
    render_json,
    render_markdown,
    render_text,
)
from infra.cli.main import app
from infra.parser import parse

runner = CliRunner()

BASE = (
    'service api {\n    image: "myapp:1.0"\n    replicas: 2\n}\n'
    'database db {\n    type: "postgres"\n}\n'
)

HEAD = (
    'service api {\n    image: "myapp:1.1"\n    replicas: 3\n}\n'
    'database db {\n    type: "postgres"\n}\n'
    'cache session {\n    type: "redis"\n}\n'
)

INSECURE = (
    'service api {\n'
    '    image: "myapp:1.0"\n'
    "    env {\n"
    '        PASSWORD: "hardcoded123"\n'
    "    }\n"
    "}\n"
)


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


class TestBuildReport:
    def test_no_base_absolute_cost_only(self):
        report = build_report(parse(BASE), source="app.infra")
        assert report.monthly_usd > 0
        assert report.base_monthly_usd is None
        assert report.delta_usd is None
        assert report.gate_passed is True

    def test_with_base_diff_and_delta(self):
        report = build_report(
            parse(HEAD),
            source="app.infra",
            base_program=parse(BASE),
            base_source="base.infra",
        )
        assert any("cache" in a for a in report.added)
        assert any("api" in c for c in report.changed)
        assert report.removed == []
        assert report.delta_usd is not None
        assert report.delta_usd > 0

    def test_removed_resources(self):
        report = build_report(
            parse(BASE),
            source="app.infra",
            base_program=parse(HEAD),
            base_source="base.infra",
        )
        assert any("session" in r for r in report.removed)
        assert report.delta_usd < 0

    def test_cost_gate_exceeded(self):
        report = build_report(parse(BASE), source="a", max_monthly_cost=0.01)
        assert report.cost_exceeded is True
        assert report.gate_passed is False

    def test_cost_gate_within_limit(self):
        report = build_report(parse(BASE), source="a", max_monthly_cost=10**9)
        assert report.cost_exceeded is False
        assert report.gate_passed is True

    def test_security_errors_fail_gate(self):
        report = build_report(
            parse(INSECURE), source="a", fail_on_security=True
        )
        assert any(f["code"] == "SEC001" for f in report.security)
        assert report.security_failed is True
        assert report.gate_passed is False

    def test_security_warning_only_does_not_fail_gate(self):
        # SEC003 (mutable tag) is a warning -> gate stays green.
        report = build_report(
            parse('service api { image: "nginx:latest" }'),
            source="a",
            fail_on_security=True,
        )
        assert any(
            f["code"] == "SEC003" and f["severity"] == "warning"
            for f in report.security
        )
        assert report.security_failed is False

    def test_security_ignored_without_flag(self):
        report = build_report(parse(INSECURE), source="a")
        assert report.security  # findings reported...
        assert report.gate_passed is True  # ...but the gate is off

    def test_reliability_findings_collected(self):
        report = build_report(parse(BASE), source="a")
        assert any(f["code"].startswith("REL") for f in report.reliability)

    def test_to_dict_schema(self):
        report = build_report(
            parse(HEAD),
            source="app.infra",
            base_program=parse(BASE),
            base_source="b.infra",
            max_monthly_cost=100.0,
            fail_on_security=True,
        )
        data = report.to_dict()
        assert data["cost"]["delta_usd"] == report.delta_usd
        assert data["gates"]["passed"] is True or data["gates"]["passed"] is False
        assert set(data["changes"]) == {"added", "removed", "changed"}

    def test_finding_without_location(self):
        # Findings lacking a SourceLocation must not crash normalisation.
        report = build_report(parse(BASE), source="a")
        for f in report.security + report.reliability:
            assert "line" in f and "hint" in f


class TestRender:
    def _report(self, **kw):
        return build_report(parse(HEAD), source="app.infra", **kw)

    def test_markdown_marker_and_sections(self):
        md = render_markdown(
            self._report(
                base_program=parse(BASE),
                base_source="base.infra",
                max_monthly_cost=1.0,
                fail_on_security=True,
            )
        )
        assert md.startswith(COMMENT_MARKER)
        assert "## 🚀 Infra Lang" in md
        assert "### 💰 Monthly cost" in md
        assert "🔺" in md  # positive delta arrow
        assert "➕" in md  # added cache
        assert "✏️" in md  # changed api
        assert "### 🚦 Gates" in md
        assert "❌ **cost gate:**" in md
        assert "✅ **security gate:**" in md

    def test_markdown_no_base_no_changes_section(self):
        md = render_markdown(self._report())
        assert "**base:**" not in md
        assert "### 📦 Changes" not in md
        assert "—" not in md.split("Monthly cost")[1].split("###")[0]

    def test_markdown_no_resource_changes(self):
        md = render_markdown(
            self._report(base_program=parse(HEAD), base_source="b")
        )
        assert "- No resource changes." in md

    def test_markdown_negative_delta(self):
        md = render_markdown(
            build_report(
                parse(BASE),
                source="a",
                base_program=parse(HEAD),
                base_source="b",
            )
        )
        assert "🟢" in md
        assert "➖" in md

    def test_markdown_security_section_with_icons(self):
        md = render_markdown(build_report(parse(INSECURE), source="a"))
        assert "### 🔒 Security findings" in md
        assert "❌ `SEC001`" in md

    def test_markdown_reliability_section(self):
        md = render_markdown(self._report())
        assert "### 🛡️ Reliability hints" in md

    def test_markdown_gates_within_limits(self):
        md = render_markdown(
            self._report(max_monthly_cost=10**9, fail_on_security=True)
        )
        assert "✅ **cost gate:**" in md

    def test_json_roundtrip(self):
        payload = json.loads(
            render_json(self._report(base_program=parse(BASE), base_source="b"))
        )
        assert payload["base"] == "b"
        assert payload["cost"]["delta_usd"] > 0
        assert payload["gates"]["passed"] is True

    def test_text_ascii_and_gates(self):
        report = build_report(
            parse(HEAD),
            source="app.infra",
            base_program=parse(BASE),
            base_source="b",
            max_monthly_cost=1.0,
            fail_on_security=True,
        )
        text = render_text(report)
        assert text.isascii()
        assert "gate: FAILED" in text
        assert "cost delta: +" in text
        assert "added: cache" in text
        assert "warning[REL" in text

    def test_text_passed_gate_line(self):
        text = render_text(build_report(parse(BASE), source="a"))
        assert "gate: PASSED" in text


class TestCiCommentCLI:
    def test_default_markdown_stdout(self, tmp_path):
        f = _write(tmp_path, "app.infra", BASE)
        result = runner.invoke(app, ["ci-comment", str(f)])
        assert result.exit_code == 0
        assert result.output.startswith(COMMENT_MARKER)

    def test_gate_status_goes_to_stderr(self, tmp_path):
        f = _write(tmp_path, "app.infra", BASE)
        result = runner.invoke(app, ["ci-comment", str(f)])
        # stdout carries ONLY the comment body (pipeable to gh pr comment)
        assert "[OK]" not in result.output.split("---")[0]

    def test_base_diff(self, tmp_path):
        head = _write(tmp_path, "app.infra", HEAD)
        base = _write(tmp_path, "base.infra", BASE)
        result = runner.invoke(app, ["ci-comment", str(head), "-b", str(base)])
        assert result.exit_code == 0
        assert "base.infra" in result.output
        assert "➕" in result.output

    def test_max_monthly_cost_exceeded_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", BASE)
        result = runner.invoke(
            app, ["ci-comment", str(f), "--max-monthly-cost", "0.01"]
        )
        assert result.exit_code == 1
        assert "FAIL" in result.output

    def test_fail_on_security_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", INSECURE)
        result = runner.invoke(app, ["ci-comment", str(f), "--fail-on-security"])
        assert result.exit_code == 1

    def test_fail_on_security_without_findings_passes(self, tmp_path):
        f = _write(tmp_path, "app.infra", BASE)
        result = runner.invoke(app, ["ci-comment", str(f), "--fail-on-security"])
        assert result.exit_code == 0

    def test_json_format(self, tmp_path):
        f = _write(tmp_path, "app.infra", HEAD)
        base = _write(tmp_path, "base.infra", BASE)
        result = runner.invoke(
            app,
            [
                "ci-comment",
                str(f),
                "--base",
                str(base),
                "--format",
                "json",
                "--max-monthly-cost",
                "100000",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output[: result.output.rindex("}") + 1])
        assert payload["gates"]["passed"] is True
        assert payload["cost"]["base_monthly_usd"] is not None

    def test_text_format(self, tmp_path):
        f = _write(tmp_path, "app.infra", BASE)
        result = runner.invoke(app, ["ci-comment", str(f), "-f", "text"])
        assert result.exit_code == 0
        assert "monthly cost:" in result.output

    def test_unknown_format_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", BASE)
        result = runner.invoke(app, ["ci-comment", str(f), "-f", "xml"])
        assert result.exit_code == 1

    def test_missing_file_exits_1(self, tmp_path):
        result = runner.invoke(app, ["ci-comment", str(tmp_path / "nope.infra")])
        assert result.exit_code == 1

    def test_missing_base_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", BASE)
        result = runner.invoke(
            app, ["ci-comment", str(f), "-b", str(tmp_path / "nope.infra")]
        )
        assert result.exit_code == 1

    def test_parse_error_exits_1(self, tmp_path):
        f = _write(tmp_path, "broken.infra", "service {{\n")
        result = runner.invoke(app, ["ci-comment", str(f)])
        assert result.exit_code == 1

    def test_base_parse_error_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", BASE)
        base = _write(tmp_path, "base.infra", "service {{\n")
        result = runner.invoke(app, ["ci-comment", str(f), "-b", str(base)])
        assert result.exit_code == 1

    def test_environment_overlay_applied(self, tmp_path):
        src = (
            'service api {\n    image: "x:1"\n    replicas: 1\n}\n'
            'environment "big" {\n'
            "    service api {\n"
            "        replicas: 3\n"
            "    }\n"
            "}\n"
        )
        f = _write(tmp_path, "app.infra", src)
        plain = runner.invoke(app, ["ci-comment", str(f), "-f", "json"])
        big = runner.invoke(
            app, ["ci-comment", str(f), "-f", "json", "-e", "big"]
        )
        assert plain.exit_code == 0 and big.exit_code == 0
        plain_cost = json.loads(plain.output[: plain.output.rindex("}") + 1])
        big_cost = json.loads(big.output[: big.output.rindex("}") + 1])
        assert big_cost["cost"]["monthly_usd"] > plain_cost["cost"]["monthly_usd"]

    def test_unknown_environment_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", BASE)
        result = runner.invoke(app, ["ci-comment", str(f), "-e", "nope"])
        assert result.exit_code == 1

    def test_help_mentions_gates(self):
        result = runner.invoke(app, ["ci-comment", "--help"])
        assert result.exit_code == 0
        clean_out = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "max-monthly-cost" in clean_out


class TestActionAssets:
    def test_action_yml_exists_and_declares_inputs(self):
        from pathlib import Path

        import yaml

        action = Path(".github/actions/infra-check/action.yml")
        assert action.exists()
        data = yaml.safe_load(action.read_text(encoding="utf-8"))
        inputs = data["inputs"]
        for name in ("files", "base-ref", "max-monthly-cost", "fail-on-security"):
            assert name in inputs
        assert data["runs"]["using"] == "composite"

    def test_ci_integration_doc_exists(self):
        from pathlib import Path

        doc = Path("docs/ci_integration.md")
        assert doc.exists()
        text = doc.read_text(encoding="utf-8")
        assert "infra ci-comment" in text
        assert "actions/infra-check" in text


@pytest.mark.parametrize("fmt", ["github-comment", "json", "text"])
def test_all_formats_exit_zero(tmp_path, fmt):
    f = tmp_path / "app.infra"
    f.write_text(BASE, encoding="utf-8")
    assert runner.invoke(app, ["ci-comment", str(f), "-f", fmt]).exit_code == 0


def test_report_dataclass_defaults():
    r = CiReport(source="x", base=None, monthly_usd=1.0, base_monthly_usd=None)
    assert r.added == [] and r.security == []
    assert r.gate_passed is True
