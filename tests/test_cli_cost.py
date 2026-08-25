"""Tests for `infra cost` command and the cost estimation module."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from infra.analyzer.cost import estimate_cost
from infra.cli.main import app
from infra.parser import parse

runner = CliRunner()


class TestCostModule:
    def test_service_cost_default(self):
        est = estimate_cost(parse('service api { image: "nginx:1.25" }'))
        assert est.total_monthly_usd > 0
        assert est.items[0].kind == "service"

    def test_service_replicas_multiply(self):
        est = estimate_cost(
            parse('service api { image: "x" replicas: 3 }')
        )
        single = estimate_cost(parse('service api { image: "x" }'))
        assert est.items[0].vcpu > single.items[0].vcpu

    def test_database_managed_fee(self):
        est = estimate_cost(parse("database db { type: postgres }"))
        assert est.items[0].managed is True
        assert est.items[0].monthly_usd >= 25

    def test_cache_managed(self):
        est = estimate_cost(parse("cache c { type: redis }"))
        assert est.items[0].managed is True

    def test_total_positive(self):
        est = estimate_cost(
            parse(
                'service api { image: "x" }\n'
                "database db { type: postgres }\n"
                "cache c { type: redis }\n"
            )
        )
        assert est.total_monthly_usd > 0
        assert len(est.items) == 3

    def test_empty_program_zero(self):
        est = estimate_cost(parse(""))
        assert est.total_monthly_usd == 0


class TestCostCLI:
    def test_cost_json_schema(self, tmp_path):
        p = tmp_path / "app.infra"
        p.write_text(
            'service api { image: "nginx:1.25" replicas: 2 }', encoding="utf-8"
        )
        result = runner.invoke(app, ["cost", str(p), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "total_monthly_usd" in data
        assert isinstance(data["total_monthly_usd"], (int, float))
        assert "breakdown" in data
        assert isinstance(data["breakdown"], list)
        if data["breakdown"]:
            item = data["breakdown"][0]
            for key in ("name", "kind", "monthly_usd", "vcpu", "ram_gb"):
                assert key in item

    def test_cost_example_table(self, tmp_path):
        p = tmp_path / "app.infra"
        p.write_text(
            'service api { image: "nginx:1.25" }\ndatabase db { type: postgres }',
            encoding="utf-8",
        )
        result = runner.invoke(app, ["cost", str(p)])
        assert result.exit_code == 0
        assert "Estimated monthly infrastructure cost" in result.stdout
        assert "TOTAL" in result.stdout

    def test_cost_currency_pln(self, tmp_path):
        p = tmp_path / "app.infra"
        p.write_text('service api { image: "nginx:1.25" }', encoding="utf-8")
        result = runner.invoke(app, ["cost", str(p), "--currency", "PLN"])
        assert result.exit_code == 0
        assert "PLN" in result.stdout

    def test_cost_missing_file(self, tmp_path):
        result = runner.invoke(app, ["cost", str(tmp_path / "nope.infra")])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_cost_from_examples(self):
        # cost on a real bundled example must produce valid JSON with a total
        ex = Path("examples/02_web_app.infra")
        result = runner.invoke(app, ["cost", str(ex), "--json"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["total_monthly_usd"] > 0
        assert len(data["breakdown"]) >= 3
