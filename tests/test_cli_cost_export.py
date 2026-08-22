"""Contract tests for `infra cost --format markdown|html` (FinOps PR reports)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from infra.analyzer.cost import estimate_cost
from infra.cli.main import app
from infra.parser import parse

runner = CliRunner()

SPEC = (
    'service api { image: "nginx:1.25" replicas: 2 }\n'
    "database db { type: postgres }\n"
)


def write_spec(tmp_path: Path, content: str = SPEC) -> Path:
    p = tmp_path / "app.infra"
    p.write_text(content, encoding="utf-8")
    return p


class TestToMarkdown:
    def test_header_and_table_columns(self):
        est = estimate_cost(parse(SPEC))
        md = est.to_markdown()
        assert "## 💰 Estimated Monthly Infrastructure Cost" in md
        assert (
            "| Resource | Kind | vCPU | RAM (GB) | Storage (GB) | Monthly (USD) |"
            in md
        )
        assert "| --- | --- | ---: | ---: | ---: | ---: |" in md

    def test_rows_per_resource(self):
        est = estimate_cost(parse(SPEC))
        md = est.to_markdown()
        assert "| api | service |" in md
        assert "| db | database |" in md

    def test_total_row_bold(self):
        est = estimate_cost(parse(SPEC))
        md = est.to_markdown()
        assert f"| **TOTAL** | | | | | **{est.total_monthly_usd:.2f}** |" in md

    def test_currency_conversion(self):
        est = estimate_cost(parse(SPEC))
        md = est.to_markdown(currency="PLN", factor=4.0)
        assert "Monthly (PLN)" in md
        assert f"**{est.total_monthly_usd * 4.0:.2f}**" in md

    def test_footer_attribution(self):
        md = estimate_cost(parse(SPEC)).to_markdown()
        assert "infra cost" in md
        assert "Not a billing-grade quote" in md

    def test_empty_program(self):
        md = estimate_cost(parse("")).to_markdown()
        assert "| **TOTAL** | | | | | **0.00** |" in md

    def test_table_rows_have_consistent_column_count(self):
        md = estimate_cost(parse(SPEC)).to_markdown()
        rows = [ln for ln in md.splitlines() if ln.startswith("|")]
        counts = {row.count("|") for row in rows}
        assert counts == {7}  # 6 columns -> 7 pipes in every row


class TestToHtml:
    def test_structure(self):
        est = estimate_cost(parse(SPEC))
        html = est.to_html()
        assert html.startswith("<table>")
        assert html.rstrip().endswith("</table>")
        assert "<thead>" in html and "<tbody>" in html
        assert "<th>Resource</th>" in html
        assert "<th>Monthly (USD)</th>" in html

    def test_rows_and_total(self):
        est = estimate_cost(parse(SPEC))
        html = est.to_html()
        assert "<td>api</td>" in html
        assert "<td>db</td>" in html
        assert f"<strong>{est.total_monthly_usd:.2f}</strong>" in html

    def test_escapes_resource_names(self):
        from infra.analyzer.cost import CostEstimate, CostItem

        est = CostEstimate(items=[CostItem(name="a<b>&c", kind="service")])
        html = est.to_html()
        assert "a&lt;b&gt;&amp;c" in html
        assert "<b>" not in html.replace("<tbody>", "")

    def test_currency_label(self):
        html = estimate_cost(parse(SPEC)).to_html(currency="EUR", factor=0.92)
        assert "Monthly (EUR)" in html


class TestCostFormatCLI:
    def test_format_markdown(self, tmp_path):
        p = write_spec(tmp_path)
        result = runner.invoke(app, ["cost", str(p), "--format", "markdown"])
        assert result.exit_code == 0
        assert "## 💰 Estimated Monthly Infrastructure Cost" in result.stdout
        assert "| api | service |" in result.stdout
        assert "**TOTAL**" in result.stdout

    def test_format_short_flag(self, tmp_path):
        p = write_spec(tmp_path)
        result = runner.invoke(app, ["cost", str(p), "-f", "markdown"])
        assert result.exit_code == 0
        assert "| api | service |" in result.stdout

    def test_format_html(self, tmp_path):
        p = write_spec(tmp_path)
        result = runner.invoke(app, ["cost", str(p), "--format", "html"])
        assert result.exit_code == 0
        assert "<table>" in result.stdout
        assert "<td>api</td>" in result.stdout

    def test_format_json_equals_json_flag(self, tmp_path):
        p = write_spec(tmp_path)
        via_format = runner.invoke(app, ["cost", str(p), "--format", "json"])
        via_flag = runner.invoke(app, ["cost", str(p), "--json"])
        assert via_format.exit_code == via_flag.exit_code == 0
        assert json.loads(via_format.stdout) == json.loads(via_flag.stdout)

    def test_format_case_insensitive(self, tmp_path):
        p = write_spec(tmp_path)
        result = runner.invoke(app, ["cost", str(p), "--format", "MARKDOWN"])
        assert result.exit_code == 0
        assert "**TOTAL**" in result.stdout

    def test_format_table_default(self, tmp_path):
        p = write_spec(tmp_path)
        result = runner.invoke(app, ["cost", str(p)])
        assert result.exit_code == 0
        assert "Estimated monthly infrastructure cost" in result.stdout

    def test_unknown_format_exit_1(self, tmp_path):
        p = write_spec(tmp_path)
        result = runner.invoke(app, ["cost", str(p), "--format", "pdf"])
        assert result.exit_code == 1
        assert "Unknown format" in result.stdout

    def test_markdown_currency(self, tmp_path):
        p = write_spec(tmp_path)
        result = runner.invoke(
            app, ["cost", str(p), "-f", "markdown", "--currency", "PLN"]
        )
        assert result.exit_code == 0
        assert "Monthly (PLN)" in result.stdout

    def test_output_to_file_markdown(self, tmp_path):
        p = write_spec(tmp_path)
        out = tmp_path / "report.md"
        result = runner.invoke(
            app, ["cost", str(p), "-f", "markdown", "-o", str(out)]
        )
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "## 💰 Estimated Monthly Infrastructure Cost" in content
        assert "Report written to" in result.stdout

    def test_output_to_file_html(self, tmp_path):
        p = write_spec(tmp_path)
        out = tmp_path / "report.html"
        result = runner.invoke(app, ["cost", str(p), "-f", "html", "-o", str(out)])
        assert result.exit_code == 0
        assert "<table>" in out.read_text(encoding="utf-8")

    def test_output_creates_parent_dirs(self, tmp_path):
        p = write_spec(tmp_path)
        out = tmp_path / "reports" / "nested" / "cost.md"
        result = runner.invoke(
            app, ["cost", str(p), "-f", "markdown", "-o", str(out)]
        )
        assert result.exit_code == 0
        assert out.exists()

    def test_output_with_table_format_rejected(self, tmp_path):
        p = write_spec(tmp_path)
        out = tmp_path / "report.txt"
        result = runner.invoke(app, ["cost", str(p), "-o", str(out)])
        assert result.exit_code == 1
        assert not out.exists()

    def test_output_json_to_file(self, tmp_path):
        p = write_spec(tmp_path)
        out = tmp_path / "cost.json"
        result = runner.invoke(app, ["cost", str(p), "-f", "json", "-o", str(out)])
        assert result.exit_code == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "total_monthly_usd" in data

    def test_missing_file_exit_1(self, tmp_path):
        result = runner.invoke(
            app, ["cost", str(tmp_path / "nope.infra"), "-f", "markdown"]
        )
        assert result.exit_code == 1

    def test_environment_overlay_with_format(self, tmp_path):
        p = tmp_path / "app.infra"
        p.write_text(
            'service api { image: "x" replicas: 1 }\n'
            'environment "prod" { service api { replicas: 4 } }\n',
            encoding="utf-8",
        )
        base = runner.invoke(app, ["cost", str(p), "-f", "json"])
        prod = runner.invoke(app, ["cost", str(p), "-f", "json", "-e", "prod"])
        assert base.exit_code == prod.exit_code == 0
        assert (
            json.loads(prod.stdout)["total_monthly_usd"]
            > json.loads(base.stdout)["total_monthly_usd"]
        )
