"""Tests for ``infra explain`` — the insight report command (v0.9.0)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from infra.cli.main import app
from infra.explain import (
    REL_IMPACT,
    SECTION_IDS,
    ExplainData,
    collect_explain_data,
    infer_arch_type,
    reliability_score,
    source_checksum,
)
from infra.explain.renderer import parse_sections, render_explain
from infra.parser import parse

runner = CliRunner()

# --------------------------------------------------------------------------- #
# Fixtures: three architecture sizes
# --------------------------------------------------------------------------- #

SMALL = """\
service web {
  image: "nginx:1.25"
  port: 80
  depends_on: [db]
}
database db {
  type: postgres
}
"""

MEDIUM = """\
service api {
  image: "registry.example.com/api:2.1"
  replicas: 2
  port: 8080
  health http("/health") { interval: 30s }
  resources {
    limits { memory: 512Mi }
  }
  depends_on: [db]
}
service web {
  image: "myapp:1.0.0"
  replicas: 1
  port: 80
  depends_on: [api]
}
database db {
  type: postgres
  size: 20Gi
  backup { enabled: true schedule: "0 2 * * *" }
}
cache sessions {
  type: redis
  persistence: true
}
"""

COMPLEX = """\
service api {
  image: "api:3.0"
  replicas: 3
  port: 8080
  ingress { host: "api.example.com" }
  network_policy { allow_from: [web] }
  env { DB_URL: "postgres://db" }
  depends_on: [db, events, cache]
}
service worker {
  image: "worker:1.4"
  replicas: 2
  depends_on: [events]
}
service web {
  image: "web:2.0"
  replicas: 2
  port: 443
  ingress { host: "example.com" }
  depends_on: [api]
}
database db {
  type: postgres
  size: 50Gi
  replicas: 3
  backup { enabled: true }
}
queue events {
  type: kafka
  replicas: 3
}
cache cache {
  type: redis
}
storage assets {
  type: s3
  size: 100Gi
}
"""

ALL_SOURCES = {"small": SMALL, "medium": MEDIUM, "complex": COMPLEX}


def _write(tmp_path, name: str, source: str):
    f = tmp_path / f"{name}.infra"
    f.write_text(source, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# CLI smoke: exit codes & formats
# --------------------------------------------------------------------------- #


class TestCliBasics:
    def test_help_lists_command(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "explain" in result.output

    def test_explain_help(self):
        result = runner.invoke(app, ["explain", "--help"])
        assert result.exit_code == 0
        assert "--format" in result.output
        assert "--for" in result.output
        assert "--sections" in result.output

    @pytest.mark.parametrize("fmt", ["markdown", "text", "json"])
    @pytest.mark.parametrize("src_name", ["small", "medium", "complex"])
    def test_three_formats_x_three_sources(self, tmp_path, fmt, src_name):
        f = _write(tmp_path, src_name, ALL_SOURCES[src_name])
        result = runner.invoke(app, ["explain", str(f), "--format", fmt])
        assert result.exit_code == 0, result.output
        assert result.output.strip()

    def test_default_format_is_markdown(self, tmp_path):
        f = _write(tmp_path, "app", MEDIUM)
        result = runner.invoke(app, ["explain", str(f)])
        assert result.exit_code == 0
        assert result.output.startswith("# Architecture Insight:")

    def test_missing_file_exits_1(self, tmp_path):
        result = runner.invoke(app, ["explain", str(tmp_path / "nope.infra")])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_parse_error_exits_1(self, tmp_path):
        f = _write(tmp_path, "broken", "service {{{\n")
        result = runner.invoke(app, ["explain", str(f)])
        assert result.exit_code == 1
        assert "Cannot parse" in result.output

    def test_unknown_format_exits_1(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(app, ["explain", str(f), "--format", "yaml"])
        assert result.exit_code == 1
        assert "Unknown format" in result.output

    def test_unknown_audience_exits_1(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(app, ["explain", str(f), "--for", "robot"])
        assert result.exit_code == 1
        assert "Unknown audience" in result.output

    def test_unknown_section_exits_1(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(
            app, ["explain", str(f), "--sections", "overview,wat"]
        )
        assert result.exit_code == 1
        assert "Unknown section" in result.output

    def test_invalid_var_exits_1(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(app, ["explain", str(f), "--var", "noequals"])
        assert result.exit_code == 1
        assert "Invalid --var" in result.output

    def test_valid_var_accepted(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(
            app, ["explain", str(f), "--var", "region=eu-central-1"]
        )
        assert result.exit_code == 0

    def test_output_file(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        out = tmp_path / "report.md"
        result = runner.invoke(app, ["explain", str(f), "-o", str(out)])
        assert result.exit_code == 0
        content = out.read_text(encoding="utf-8")
        assert content.startswith("# Architecture Insight:")
        assert content.endswith("\n")

    def test_output_file_adds_trailing_newline(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        out = tmp_path / "report.txt"
        # json --for ai is compact without trailing newline in the payload
        result = runner.invoke(
            app, ["explain", str(f), "-f", "json", "--for", "ai", "-o", str(out)]
        )
        assert result.exit_code == 0
        assert out.read_text(encoding="utf-8").endswith("\n")

    def test_validation_errors_still_render(self, tmp_path):
        f = _write(
            tmp_path, "bad", 'service bad {\n  image: "x:1"\n  replicas: -3\n}\n'
        )
        result = runner.invoke(app, ["explain", str(f)])
        assert result.exit_code == 0
        assert "E011" in result.output
        assert "Errors" in result.output

    def test_environment_overlay(self, tmp_path):
        src = (
            'service api {\n  image: "a:1"\n  replicas: 1\n}\n'
            'environment "prod" {\n  service api { replicas: 5 }\n}\n'
        )
        f = _write(tmp_path, "env", src)
        result = runner.invoke(
            app, ["explain", str(f), "-f", "json", "-e", "prod"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["services"][0]["replicas"] == 5


# --------------------------------------------------------------------------- #
# Section selection
# --------------------------------------------------------------------------- #


class TestSectionSelection:
    @pytest.mark.parametrize("section", list(SECTION_IDS))
    def test_each_section_alone(self, tmp_path, section):
        f = _write(tmp_path, "app", COMPLEX)
        result = runner.invoke(
            app, ["explain", str(f), "--sections", section]
        )
        assert result.exit_code == 0, result.output
        title = {
            "overview": "## Overview",
            "services": "## Services",
            "deps": "## Dependencies",
            "cost": "## Cost Breakdown",
            "security": "## Security Warnings",
            "reliability": "## Reliability Report",
            "whatif": "## What-If Scenarios",
        }[section]
        assert title in result.output
        # no other section title leaks in
        other = {
            "overview": "## Overview",
            "services": "## Services",
            "deps": "## Dependencies",
            "cost": "## Cost Breakdown",
            "security": "## Security Warnings",
            "reliability": "## Reliability Report",
            "whatif": "## What-If Scenarios",
        }
        for key, other_title in other.items():
            if key != section:
                assert other_title not in result.output

    def test_multiple_sections_comma_separated(self, tmp_path):
        f = _write(tmp_path, "app", COMPLEX)
        result = runner.invoke(
            app, ["explain", str(f), "--sections", "services,cost"]
        )
        assert result.exit_code == 0
        assert "## Services" in result.output
        assert "## Cost Breakdown" in result.output
        assert "## Overview" not in result.output

    def test_section_order_is_canonical(self):
        assert parse_sections("cost,overview") == ["overview", "cost"]

    def test_empty_selection_raises(self):
        with pytest.raises(ValueError):
            parse_sections(" , ")

    def test_all_expands_everything(self):
        assert parse_sections("all") == list(SECTION_IDS)

    def test_sections_in_json_output(self, tmp_path):
        f = _write(tmp_path, "app", COMPLEX)
        result = runner.invoke(
            app, ["explain", str(f), "-f", "json", "--sections", "cost"]
        )
        payload = json.loads(result.output)
        assert "cost" in payload
        assert "services" not in payload


# --------------------------------------------------------------------------- #
# --for ai determinism & structure
# --------------------------------------------------------------------------- #


class TestAiAudience:
    def test_ai_json_deterministic(self, tmp_path):
        f = _write(tmp_path, "app", COMPLEX)
        r1 = runner.invoke(app, ["explain", str(f), "-f", "json", "--for", "ai"])
        r2 = runner.invoke(app, ["explain", str(f), "-f", "json", "--for", "ai"])
        assert r1.exit_code == r2.exit_code == 0
        assert r1.output == r2.output

    def test_ai_json_has_meta_and_summary(self, tmp_path):
        f = _write(tmp_path, "app", MEDIUM)
        result = runner.invoke(
            app, ["explain", str(f), "-f", "json", "--for", "ai"]
        )
        payload = json.loads(result.output)
        meta = payload["_meta"]
        assert meta["language"] == "infra-lang"
        assert "generator_version" in meta
        assert meta["checksum"] == source_checksum(MEDIUM)
        assert "timestamp" in meta  # derived from file mtime -> deterministic
        summary = payload["_summary"]
        assert 3 <= len(summary) <= 5
        assert all(isinstance(s, str) for s in summary)

    def test_ai_json_is_compact(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(
            app, ["explain", str(f), "-f", "json", "--for", "ai"]
        )
        # compact separators: no ", " or ": " padding inside the payload
        assert "\n" not in result.output.strip()
        assert '", "' not in result.output

    def test_ai_text_banner(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(app, ["explain", str(f), "--for", "ai"])
        assert result.exit_code == 0
        assert result.output.startswith("[meta] language=infra-lang")
        assert "[summary] This is a" in result.output

    def test_ai_markdown_banner(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(
            app, ["explain", str(f), "-f", "markdown", "--for", "ai"]
        )
        assert result.exit_code == 0
        lines = result.output.splitlines()
        assert lines[0].startswith("[meta]")
        assert lines[1].startswith("[summary]")

    def test_renderer_without_now_omits_timestamp(self):
        data = collect_explain_data(parse(SMALL), source=SMALL, project="x")
        out = render_explain(data, output_format="json", audience="ai", now=None)
        assert "timestamp" not in json.loads(out)["_meta"]

    def test_human_json_is_indented(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(app, ["explain", str(f), "-f", "json"])
        assert "\n  " in result.output  # indent=2 structure present


# --------------------------------------------------------------------------- #
# Content correctness
# --------------------------------------------------------------------------- #


class TestContent:
    def test_services_table_rows(self, tmp_path):
        f = _write(tmp_path, "app", MEDIUM)
        result = runner.invoke(app, ["explain", str(f)])
        assert "`api` | registry.example.com/api:2.1 | 2 | 8080" in result.output
        assert "| ok |" in result.output  # health ok

    def test_security_section_lists_findings(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(app, ["explain", str(f)])
        assert "SEC009" in result.output  # docker hub image

    def test_security_section_empty(self, tmp_path):
        src = (
            'service api {\n  image: "registry.example.com/api:2.0"\n'
            "  replicas: 2\n  port: 8080\n"
            '  health http("/health")\n'
            "  resources {\n    limits { memory: 512Mi }\n  }\n"
            "  security {\n    user: 1000\n  }\n"
            "  lifecycle {\n    preStop {\n"
            "      exec: [\"sleep\", \"5\"]\n"
            "    }\n  }\n}\n"
        )
        f = _write(tmp_path, "secure", src)
        result = runner.invoke(app, ["explain", str(f)])
        assert result.exit_code == 0
        assert "No security warnings." in result.output

    def test_reliability_section_shows_impact(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(app, ["explain", str(f)])
        assert "[high impact]" in result.output  # REL004 no health check

    def test_deps_section_lists_edges(self, tmp_path):
        f = _write(tmp_path, "app", COMPLEX)
        result = runner.invoke(app, ["explain", str(f)])
        assert "`web` depends on: `api`" in result.output

    def test_spof_reported(self, tmp_path):
        src = (
            'service a {\n  image: "a:1"\n  depends_on: [core]\n}\n'
            'service b {\n  image: "b:1"\n  depends_on: [core]\n}\n'
            'service core {\n  image: "c:1"\n  replicas: 1\n}\n'
        )
        f = _write(tmp_path, "spof", src)
        result = runner.invoke(app, ["explain", str(f)])
        assert "`core`" in result.output
        assert "Single points of failure" in result.output

    def test_no_spof_message(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(app, ["explain", str(f)])
        assert "No single points of failure detected." in result.output

    def test_cost_categories_and_shares(self, tmp_path):
        f = _write(tmp_path, "app", MEDIUM)
        result = runner.invoke(app, ["explain", str(f), "--sections", "cost"])
        assert "**Total: $" in result.output
        assert "- compute: $" in result.output
        assert "- managed: $" in result.output
        assert "%" in result.output

    def test_whatif_failure_impact(self, tmp_path):
        f = _write(tmp_path, "app", COMPLEX)
        result = runner.invoke(app, ["explain", str(f), "--sections", "whatif"])
        assert "If `api` fails → also down: `web`" in result.output

    def test_whatif_scale_delta(self, tmp_path):
        f = _write(tmp_path, "app", MEDIUM)
        result = runner.invoke(app, ["explain", str(f), "--sections", "whatif"])
        # scaling web (1->2 replicas, default 1 vCPU/512Mi) costs +$32.00
        assert "`web` replicas 1→2: cost +$32.00/mo" in result.output

    def test_text_format_tables(self, tmp_path):
        f = _write(tmp_path, "app", COMPLEX)
        result = runner.invoke(app, ["explain", str(f), "-f", "text"])
        assert "== Overview" in result.output
        assert "SERVICE" in result.output
        assert "MONTHLY" in result.output
        assert "By category:" in result.output

    def test_json_structure_full(self, tmp_path):
        f = _write(tmp_path, "app", COMPLEX)
        result = runner.invoke(app, ["explain", str(f), "-f", "json"])
        payload = json.loads(result.output)
        assert payload["project"] == "app"
        assert payload["arch_type"] == "event-driven"
        for key in (
            "overview",
            "services",
            "dependencies",
            "cost",
            "security",
            "reliability",
            "what_if",
        ):
            assert key in payload


# --------------------------------------------------------------------------- #
# Snapshot tests (deterministic, no timestamps in human output)
# --------------------------------------------------------------------------- #

SNAPSHOT_MD = (
    "# Architecture Insight: snap\n"
    "\n"
    "## Overview\n"
    "\n"
    "- **Project:** snap\n"
    "- **Architecture type:** monolithic\n"
    "- **Topology:** 1 service(s), 1 database(s), 0 queue(s), 0 cache(s), "
    "0 storage(s), 0 pipeline(s)\n"
    "- **Dependency edges:** 1\n"
    "- **Technologies:** nginx, postgres\n"
    "- **Top costs:**\n"
    "  - `db` (database) — $102.00/mo\n"
    "  - `app` (service) — $32.00/mo\n"
    "\n"
    "## Services\n"
    "\n"
    "| Service | Image | Replicas | Port | Monthly | Health | Sec | Rel |\n"
    "| --- | --- | ---: | ---: | ---: | --- | --- | --- |\n"
    "| `app` | nginx:1.25 | 1 | 80 | $32.00 | missing | B | C |\n"
    "\n"
)

SNAP_SOURCE = """\
service app {
  image: "nginx:1.25"
  port: 80
  depends_on: [db]
}
database db {
  type: postgres
  size: 10Gi
}
"""


class TestSnapshots:
    def test_markdown_prefix_snapshot(self, tmp_path):
        f = _write(tmp_path, "snap", SNAP_SOURCE)
        result = runner.invoke(
            app, ["explain", str(f), "--sections", "overview,services"]
        )
        assert result.exit_code == 0
        assert result.output == SNAPSHOT_MD

    def test_text_snapshot_contains_fixed_layout(self, tmp_path):
        f = _write(tmp_path, "snap", SNAP_SOURCE)
        result = runner.invoke(
            app, ["explain", str(f), "-f", "text", "--sections", "overview"]
        )
        assert result.exit_code == 0
        expected = (
            "ARCHITECTURE INSIGHT: snap\n"
            "\n"
            "== Overview ================================================\n"
            "\n"
            "Project:        snap\n"
            "Architecture:   monolithic\n"
            "Topology:       1 services, 1 db, 0 queues, 0 caches, "
            "0 storages, 0 pipelines\n"
            "Dependencies:   1 edge(s)\n"
            "Technologies:   nginx, postgres\n"
            "Top costs:\n"
            "  - db (database): $102.00/mo\n"
            "  - app (service): $32.00/mo\n"
            "\n"
        )
        assert result.output == expected


# --------------------------------------------------------------------------- #
# Engine unit tests (what-if with mocked parser, grades, scores, arch types)
# --------------------------------------------------------------------------- #


class TestEngine:
    def test_whatif_delta_with_mocked_parser(self, tmp_path, monkeypatch):
        """Mocked parser: verify computed (not guessed) cost/rel deltas."""
        from infra.cli import explain_cmd

        program = parse(MEDIUM)

        def fake_parse_file(_path):
            return program

        monkeypatch.setattr(explain_cmd, "parse_file", fake_parse_file)
        f = _write(tmp_path, "mocked", "ignored")
        result = runner.invoke(
            app, ["explain", str(f), "-f", "json", "--sections", "whatif"]
        )
        assert result.exit_code == 0
        what_if = json.loads(result.output)["what_if"]
        api = next(w for w in what_if["scale_x2"] if w["name"] == "api")
        assert api["current_replicas"] == 2
        assert api["new_replicas"] == 4
        # api declares only a memory limit (512Mi, no cpu): 2 replicas cost
        # $4/mo of RAM; doubling to 4 replicas adds another $4
        assert api["cost_delta_usd"] == 4.0
        assert isinstance(api["reliability_delta"], int)

    def test_failure_blast_is_transitive(self):
        data = collect_explain_data(parse(COMPLEX), source=COMPLEX, project="c")
        api = next(w for w in data.whatif_failure if w["target"] == "api")
        assert api["affected"] == ["web"]
        events = next(w for w in data.whatif_failure if w["target"] == "worker")
        assert events["affected"] == []

    def test_infer_arch_type_rules(self):
        assert infer_arch_type(parse(SMALL)) == "monolithic"
        assert infer_arch_type(parse(COMPLEX)) == "event-driven"
        pipelines = 'pipeline ci {\n  trigger { branches: [main] }\n}\n'
        assert infer_arch_type(parse(pipelines)) == "CI/CD-first"
        three_ingress = "".join(
            f'service s{i} {{\n  image: "x:1"\n'
            f'  ingress {{ host: "h{i}.example.com" }}\n}}\n'
            for i in range(3)
        )
        assert infer_arch_type(parse(three_ingress)) == "microservices"
        plain = 'service a {\n  image: "x:1"\n}\nservice b {\n  image: "y:1"\n}\n'
        assert infer_arch_type(parse(plain)) == "service-oriented"

    def test_reliability_score_clamped(self):
        from infra.analyzer.reliability import ReliabilityFinding

        many = [ReliabilityFinding(code="REL004", message="x") for _ in range(15)]
        assert reliability_score(many) == 0
        assert reliability_score([]) == 100

    def test_rel_impact_default_medium_for_unknown(self):
        from infra.analyzer.reliability import ReliabilityFinding

        findings = [ReliabilityFinding(code="REL999", message="x")]
        assert REL_IMPACT.get("REL999", "medium") == "medium"
        assert reliability_score(findings) == 95

    def test_summary_sentences_shape(self):
        data = collect_explain_data(parse(MEDIUM), source=MEDIUM, project="m")
        sentences = data.summary_sentences
        assert sentences[0].startswith("This is a")
        assert "Primary tech:" in sentences[1]
        assert "Est. monthly cost: $" in sentences[2]
        assert "Key risks:" in sentences[3]

    def test_summary_no_risks(self):
        src = (
            'service api {\n  image: "reg.local/api:1.0"\n  replicas: 2\n'
            "  port: 8080\n  health http(\"/h\")\n"
            "  resources {\n    limits { memory: 256Mi }\n  }\n"
            "  security {\n    user: 1000\n  }\n"
            "  lifecycle {\n    preStop {\n"
            "      exec: [\"sleep\", \"1\"]\n"
            "    }\n  }\n}\n"
        )
        data = collect_explain_data(parse(src), source=src, project="x")
        assert any("none detected" in s for s in data.summary_sentences)

    def test_source_checksum_stable(self):
        assert source_checksum("abc") == source_checksum("abc")
        assert source_checksum("abc") != source_checksum("abd")
        assert len(source_checksum("abc")) == 12

    def test_explain_data_defaults(self):
        d = ExplainData(
            project="p",
            checksum="c",
            arch_type="t",
            tech_stack=[],
            counts={},
            total_dependencies=0,
            top_costs=[],
        )
        assert d.services == []
        assert d.cost_total_usd == 0.0
        # empty tech stack -> 'custom images'
        assert "custom images" in d.summary_sentences[1]

    def test_empty_program(self, tmp_path):
        f = _write(tmp_path, "empty", "# only a comment\n")
        result = runner.invoke(app, ["explain", str(f)])
        assert result.exit_code == 0
        assert "*(no services defined)*" in result.output
        assert "no billable resources" in result.output
        assert "*(no services to simulate)*" in result.output

    def test_service_with_build_and_no_port(self, tmp_path):
        src = 'service app {\n  build { context: "." }\n}\n'
        f = _write(tmp_path, "buildy", src)
        result = runner.invoke(app, ["explain", str(f)])
        assert result.exit_code == 0
        assert "(build)" in result.output

    def test_findings_without_location(self):
        from infra.explain.renderer import _loc

        assert _loc({"code": "X", "line": None, "file": None}) == ""
        assert _loc({"code": "X", "line": 3, "file": "a.infra"}) == "a.infra:3"
        assert _loc({"code": "X", "line": 3, "file": None}) == "<source>:3"


# --------------------------------------------------------------------------- #
# Edge cases: full branch coverage of the engine and renderer
# --------------------------------------------------------------------------- #


SPOF_SOURCE = (
    'service a {\n  image: "a:1"\n  depends_on: [core]\n}\n'
    'service b {\n  image: "b:1"\n  depends_on: [core]\n}\n'
    'service core {\n  image: "c:1"\n  replicas: 1\n}\n'
)

ALL_BLOCKS_SOURCE = """\
network_policy "strict" {
  target: api
  block_all_ingress: true
}
secret_store "vault" {
  provider: vault
}
pipeline deploy {
  trigger { schedule: "0 3 * * *" branches: [main] }
  stages {
    build: {
      runsOn: "ubuntu-latest"
      steps {
        compile: { run: "make build" }
      }
    }
  }
}
storage assets {
  type: s3
  size: 10Gi
}
service api {
  image: "api:1.0"
  replicas: 2
}
"""


class TestEdgeCases:
    def test_grade_f_for_five_security_findings(self, tmp_path):
        src = (
            'service bad {\n  image: "myapp:latest"\n  replicas: 1\n'
            "  security { user: 0 privileged: true }\n"
            '  env { DB_PASSWORD: "abc123" }\n}\n'
        )
        f = _write(tmp_path, "badsec", src)
        result = runner.invoke(
            app, ["explain", str(f), "--sections", "services"]
        )
        assert result.exit_code == 0
        assert "| F |" in result.output

    def test_display_image_dash_without_image_or_build(self):
        from infra.explain import _display_image
        from infra.parser import ast_nodes as n

        svc = n.ServiceDef(name="x")
        assert _display_image(svc) == "-"

    def test_dependency_cycle_does_not_hang_blast_radius(self):
        src = (
            'service a {\n  image: "a:1"\n  depends_on: [b]\n}\n'
            'service b {\n  image: "b:1"\n  depends_on: [a]\n}\n'
        )
        data = collect_explain_data(parse(src), source=src, project="cyc")
        fa = next(w for w in data.whatif_failure if w["target"] == "a")
        fb = next(w for w in data.whatif_failure if w["target"] == "b")
        assert fa["affected"] == ["b"]
        assert fb["affected"] == ["a"]

    def test_summary_sentences_include_spof(self):
        data = collect_explain_data(
            parse(SPOF_SOURCE), source=SPOF_SOURCE, project="s"
        )
        assert data.spofs[0]["name"] == "core"
        assert any(
            "Single points of failure: core." in s
            for s in data.summary_sentences
        )

    def test_counts_network_policy_secret_store_pipeline_storage(self):
        data = collect_explain_data(
            parse(ALL_BLOCKS_SOURCE), source=ALL_BLOCKS_SOURCE, project="all"
        )
        assert data.counts["network_policies"] == 1
        assert data.counts["secret_stores"] == 1
        assert data.counts["pipelines"] == 1
        assert data.counts["storages"] == 1

    def test_duplicate_block_names_deduplicate_targets(self):
        src = (
            'service api {\n  image: "a:1"\n}\n'
            'database api {\n  type: postgres\n}\n'
        )
        # duplicate names are a validation error, but the collector must stay
        # total and keep the target list de-duplicated
        data = collect_explain_data(parse(src), source=src, project="dup")
        from infra.explain import _dep_targets

        program = parse(src)
        assert _dep_targets(program) == ["api"]
        assert data is not None

    def test_text_format_empty_program_covers_all_sections(self, tmp_path):
        f = _write(tmp_path, "empty", "# nothing\n")
        result = runner.invoke(app, ["explain", str(f), "-f", "text"])
        assert result.exit_code == 0
        assert "(no services defined)" in result.output
        assert "no billable resources" in result.output
        assert "(none)" in result.output  # security + reliability
        assert "(no services to simulate)" in result.output

    def test_text_format_validation_errors_section(self, tmp_path):
        f = _write(
            tmp_path, "bad", 'service bad {\n  image: "x:1"\n  replicas: -3\n}\n'
        )
        result = runner.invoke(app, ["explain", str(f), "-f", "text"])
        assert result.exit_code == 0
        assert "== Errors" in result.output
        assert "E011" in result.output

    def test_json_payload_has_errors_key(self, tmp_path):
        f = _write(
            tmp_path, "bad", 'service bad {\n  image: "x:1"\n  replicas: -3\n}\n'
        )
        result = runner.invoke(app, ["explain", str(f), "-f", "json"])
        payload = json.loads(result.output)
        assert payload["errors"][0]["code"] == "E011"

    def test_text_format_spof_listing(self, tmp_path):
        f = _write(tmp_path, "spof", SPOF_SOURCE)
        result = runner.invoke(app, ["explain", str(f), "-f", "text"])
        assert "SINGLE POINTS OF FAILURE:" in result.output
        assert "core: 1 replica(s), 2 dependent(s)" in result.output

    def test_text_whatif_no_other_service_affected(self, tmp_path):
        f = _write(tmp_path, "app", SMALL)
        result = runner.invoke(
            app, ["explain", str(f), "-f", "text", "--sections", "whatif"]
        )
        assert "web fails -> no other service affected" in result.output

