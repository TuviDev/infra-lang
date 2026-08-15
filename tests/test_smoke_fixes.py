"""Regression tests for bugs surfaced by the v0.1.0 release smoke test."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from infra import parse, validate
from infra.cli.main import app

runner = CliRunner()


class TestHealthColonVariant:
    def test_health_shorthand_with_colon_parses(self):
        """`health: http(\"/\")` (colon form) must parse like `health http(\"/\")`."""
        p = parse('service hello { image: "nginx:1.25.3" port: 80 health: http("/") }')
        svc = next(
            s
            for s in p.statements
            if getattr(getattr(s, "location", None), "file", "") != "<prelude>"
        )
        assert svc.health is not None
        assert svc.health.kind == "http"

    def test_health_colon_validation_clean(self):
        r = validate(parse('service hello { image: "x:1" health: http("/") }'))
        assert r.is_valid


class TestDocsSkipPrelude:
    def test_docs_omits_prelude_builtins(self, tmp_path):
        f = tmp_path / "t.infra"
        f.write_text('service hello { image: "nginx:1.25.3" port: 80 }')
        result = runner.invoke(app, ["docs", str(f)])
        assert result.exit_code == 0
        assert "**service** `hello`" in result.output
        # built-in prelude constants must not leak into the inventory
        assert "MANAGED_BY" not in result.output
        assert "variabledecl" not in result.output


class TestCleanParseErrors:
    def test_validate_unparseable_exits_1_with_clean_message(self, tmp_path):
        f = tmp_path / "broken.infra"
        f.write_text('service api { image: "x"\n')  # unclosed brace
        result = runner.invoke(app, ["validate", str(f)])
        assert result.exit_code == 1
        assert "error[PARSE]" in result.output
        # must NOT dump a raw python traceback
        assert "Traceback" not in result.output

    def test_validate_json_reports_parse_error(self, tmp_path):
        f = tmp_path / "broken.infra"
        f.write_text('service api { image: "x"\n')
        result = runner.invoke(app, ["validate", str(f), "--format", "json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["valid"] is False
        assert data["errors"][0]["code"] == "PARSE"
