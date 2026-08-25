"""Contract tests for the `--max-cost` FinOps guardrail (v0.4.3).

`infra validate <file> --max-cost <USD>` and `infra check <file> --max-cost
<USD>` compute the static monthly cost estimate and fail the CI/CD pipeline
(exit code 1, COST_EXCEEDED) when the budget is exceeded.
"""

from __future__ import annotations

import json
from pathlib import Path

from infra.analyzer.cost import (
    COST_EXCEEDED_CODE,
    COST_EXCEEDED_HINT,
    budget_exceeded_message,
    estimate_cost,
)
from infra.cli.main import app
from infra.parser import parse
from typer.testing import CliRunner

runner = CliRunner()

#: $3.48/month: 0.1 vCPU * $30 + 0.12 GB * $4 (128Mi rounds down to 0.12 GB).
CHEAP = """\
service tiny {
    image: "alpine:3.20"
    resources {
        limits { cpu: 100m, memory: 128Mi }
    }
}
"""

#: Exactly $330.00/month: app 3 * (2 vCPU * $30 + 4 GB * $4) = $228
#: plus postgres (2 vCPU, 4 GB, 10 GB storage, managed fee) = $102.
COSTLY = """\
service app {
    image: "myapi:v1.1"
    replicas: 3
    resources {
        limits { cpu: 2000m, memory: 4Gi }
    }
}

database db {
    type: postgres
    storage: 10Gi
}
"""

#: $76/month base; the "prod" overlay scales to 5 replicas -> $380/month.
OVERLAY = """\
service app {
    image: "myapi:v1.1"
    resources {
        limits { cpu: 2000m, memory: 4Gi }
    }
}

environment "prod" {
    service app {
        replicas: 5
    }
}
"""


def write_spec(tmp_path: Path, content: str = COSTLY, name: str = "app.infra") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


class TestBudgetHelperUnit:
    """Unit-level contract of budget_exceeded_message()."""

    def test_cost_math_is_stable(self):
        """The specs above must keep their documented totals."""
        assert estimate_cost(parse(COSTLY)).total_monthly_usd == 330.0
        assert estimate_cost(parse(CHEAP)).total_monthly_usd == 3.48

    def test_within_budget_returns_none(self):
        assert budget_exceeded_message(parse(COSTLY), 500.0) is None

    def test_exact_boundary_is_within_budget(self):
        assert budget_exceeded_message(parse(COSTLY), 330.0) is None

    def test_over_budget_message_mentions_both_amounts(self):
        msg = budget_exceeded_message(parse(COSTLY), 200.0)
        assert msg is not None
        assert "$330.00" in msg
        assert "$200.00" in msg

    def test_constants_documented_shape(self):
        assert COST_EXCEEDED_CODE == "COST_EXCEEDED"
        assert "budget" in COST_EXCEEDED_HINT


class TestSemanticValidatorMaxCost:
    """The guardrail is a first-class validation error (COST_EXCEEDED)."""

    def test_error_code_hint_and_no_location(self):
        from infra.analyzer.validator import SemanticValidator

        result = SemanticValidator().validate(parse(COSTLY), max_cost=200.0)
        assert not result.is_valid
        cost_errors = [e for e in result.errors if e.code == "COST_EXCEEDED"]
        assert len(cost_errors) == 1
        err = cost_errors[0]
        assert err.hint == COST_EXCEEDED_HINT
        assert err.location is None
        assert "$330.00" in err.message
        assert "$200.00" in err.message

    def test_no_error_within_budget(self):
        from infra.analyzer.validator import SemanticValidator

        result = SemanticValidator().validate(parse(COSTLY), max_cost=500.0)
        assert not any(e.code == "COST_EXCEEDED" for e in result.errors)

    def test_default_max_cost_disabled(self):
        from infra.analyzer.validator import SemanticValidator

        result = SemanticValidator().validate(parse(COSTLY))
        assert not any(e.code == "COST_EXCEEDED" for e in result.errors)


class TestValidateMaxCostCLI:
    def test_within_budget_passes(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["validate", str(src), "--max-cost", "500"])
        assert result.exit_code == 0
        assert "COST_EXCEEDED" not in result.stdout

    def test_over_budget_fails_with_hint(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["validate", str(src), "--max-cost", "200"])
        assert result.exit_code == 1
        assert "error[COST_EXCEEDED]" in result.stdout
        assert "$330.00" in result.stdout
        assert "$200.00" in result.stdout
        assert (
            "Hint: Reduce CPU/RAM requests or database instances to fit budget"
            in result.stdout
        )

    def test_exact_boundary_passes(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["validate", str(src), "--max-cost", "330"])
        assert result.exit_code == 0

    def test_cheap_spec_under_tiny_budget(self, tmp_path):
        src = write_spec(tmp_path, CHEAP)
        result = runner.invoke(app, ["validate", str(src), "--max-cost", "10"])
        assert result.exit_code == 0
        assert "COST_EXCEEDED" not in result.stdout

    def test_cheap_spec_fails_under_absurd_budget(self, tmp_path):
        src = write_spec(tmp_path, CHEAP)
        result = runner.invoke(app, ["validate", str(src), "--max-cost", "1"])
        assert result.exit_code == 1
        assert "COST_EXCEEDED" in result.stdout

    def test_json_output_carries_code_and_hint(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(
            app, ["validate", str(src), "--max-cost", "200", "--json"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["valid"] is False
        cost_errors = [e for e in data["errors"] if e["code"] == "COST_EXCEEDED"]
        assert len(cost_errors) == 1
        assert cost_errors[0]["hint"] == COST_EXCEEDED_HINT
        assert cost_errors[0]["severity"] == "error"

    def test_format_json_output(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(
            app, ["validate", str(src), "--max-cost", "200", "--format", "json"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["valid"] is False
        assert any(e["code"] == "COST_EXCEEDED" for e in data["errors"])

    def test_format_github_output(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(
            app, ["validate", str(src), "--max-cost", "200", "--format", "github"]
        )
        assert result.exit_code == 1
        assert "Estimated monthly cost $330.00" in result.stdout

    def test_without_max_cost_unchanged(self, tmp_path):
        """No flag -> no guardrail, even for an expensive spec."""
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["validate", str(src)])
        assert result.exit_code == 0
        assert "COST_EXCEEDED" not in result.stdout


class TestValidateMaxCostEnvOverlay:
    def test_overlay_over_budget_fails(self, tmp_path):
        """`infra validate app.infra -e prod --max-cost 100` prices prod."""
        src = write_spec(tmp_path, OVERLAY)
        result = runner.invoke(
            app, ["validate", str(src), "-e", "prod", "--max-cost", "100"]
        )
        assert result.exit_code == 1
        assert "COST_EXCEEDED" in result.stdout
        assert "$380.00" in result.stdout

    def test_base_within_same_budget_passes(self, tmp_path):
        src = write_spec(tmp_path, OVERLAY)
        result = runner.invoke(app, ["validate", str(src), "--max-cost", "100"])
        assert result.exit_code == 0

    def test_overlay_within_its_own_budget_passes(self, tmp_path):
        src = write_spec(tmp_path, OVERLAY)
        result = runner.invoke(
            app, ["validate", str(src), "-e", "prod", "--max-cost", "500"]
        )
        assert result.exit_code == 0

    def test_unknown_overlay_still_errors(self, tmp_path):
        src = write_spec(tmp_path, OVERLAY)
        result = runner.invoke(
            app, ["validate", str(src), "-e", "nope", "--max-cost", "100"]
        )
        assert result.exit_code == 1
        assert "ENV" in result.stdout


class TestCheckMaxCostCLI:
    def test_within_budget_passes(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["check", str(src), "--max-cost", "500"])
        assert result.exit_code == 0
        assert "[OK] 1 file(s) syntactically valid" in result.stdout

    def test_over_budget_fails_with_code_and_hint(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["check", str(src), "--max-cost", "200"])
        assert result.exit_code == 1
        assert "error[COST_EXCEEDED]" in result.stdout
        assert "$330.00" in result.stdout
        assert (
            "Hint: Reduce CPU/RAM requests or database instances to fit budget"
            in result.stdout
        )

    def test_exact_boundary_passes(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["check", str(src), "--max-cost", "330"])
        assert result.exit_code == 0

    def test_without_max_cost_unchanged(self, tmp_path):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["check", str(src)])
        assert result.exit_code == 0
        assert "[OK]" in result.stdout

    def test_parse_error_still_reported(self, tmp_path):
        bad = write_spec(tmp_path, "service {{{", name="bad.infra")
        result = runner.invoke(app, ["check", str(bad), "--max-cost", "500"])
        assert result.exit_code == 1
        assert "COST_EXCEEDED" not in result.stdout

    def test_multiple_files_checked_independently(self, tmp_path):
        cheap = write_spec(tmp_path, CHEAP, name="cheap.infra")
        costly = write_spec(tmp_path, COSTLY, name="costly.infra")
        result = runner.invoke(
            app, ["check", str(cheap), str(costly), "--max-cost", "200"]
        )
        assert result.exit_code == 1
        assert "costly.infra" in result.stdout
        assert "COST_EXCEEDED" in result.stdout

    def test_multiple_files_all_within_budget(self, tmp_path):
        a = write_spec(tmp_path, CHEAP, name="a.infra")
        b = write_spec(tmp_path, CHEAP, name="b.infra")
        result = runner.invoke(app, ["check", str(a), str(b), "--max-cost", "10"])
        assert result.exit_code == 0
        assert "[OK] 2 file(s) syntactically valid" in result.stdout
