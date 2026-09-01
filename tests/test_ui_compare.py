"""Side-by-side environment comparison (`infra ui/serve --compare`) — v0.5.5."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from infra.analyzer.environments import EnvironmentNotFoundError
from infra.analyzer.ui_generator import generate_compare_html
from infra.cli.main import app
from infra.parser import parse

runner = CliRunner()

SRC = """\
service web {
  image: "nginx:1.25"
  replicas: 1
  port 8080:80
  env { LOG_LEVEL: "debug" }
  resources { requests: {cpu: 250m, memory: 256Mi} }
}
database db {
  type: postgres
  version: 16
  storage: 10Gi
}
environment "prod" {
  service web {
    replicas: 5
    env { LOG_LEVEL: "info", LOG_REGION: "eu-cn" }
    resources { limits: {cpu: 1000m, memory: 1Gi} }
  }
}
environment "dev" {
  service web {
    replicas: 1
    env { LOG_LEVEL: "debug" }
  }
}
"""


def _write(tmp_path: Path, content: str = SRC) -> Path:
    f = tmp_path / "app.infra"
    f.write_text(content, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# Generator (unit)
# --------------------------------------------------------------------------- #


class TestGenerateCompareHtml:
    def test_two_panels_rendered(self):
        html = generate_compare_html(parse(SRC), "base", "prod")
        assert "cmp-grid" in html
        assert html.count('class="cmp-panel"') == 2
        assert "<b>base</b> vs" in html
        assert "<b>prod</b>" in html

    def test_diff_rows_changed(self):
        html = generate_compare_html(parse(SRC), "base", "prod")
        assert 'class="cmp-changed"' in html
        assert "replicas" in html

    def test_env_var_diff_rows(self):
        html = generate_compare_html(parse(SRC), "base", "prod")
        assert "env.LOG_LEVEL" in html
        assert "debug" in html and "info" in html
        assert "LOG_REGION" in html

    def test_resources_diff_row(self):
        html = generate_compare_html(parse(SRC), "base", "prod")
        assert "resources.limits.cpu" in html

    def test_diff_is_directional(self):
        # swapped sides swap the old/new columns of every row
        html = generate_compare_html(parse(SRC), "prod", "base")
        assert "replicas" in html
        assert "<td>5</td><td>1</td>" in html

    def test_finops_delta_row(self):
        html = generate_compare_html(parse(SRC), "base", "prod")
        assert "(finops)" in html
        assert "est. monthly" in html

    def test_cost_estimate_in_panels(self):
        html = generate_compare_html(parse(SRC), "base", "prod")
        assert html.count("estimated:") == 2
        assert "/mo" in html

    def test_empty_state_when_identical(self):
        html = generate_compare_html(parse(SRC), "prod", "prod")
        assert "No differences between" in html
        assert "identical workloads" in html
        assert 'class="cmp-table"' not in html

    def test_no_diff_when_overlay_is_noop(self):
        html = generate_compare_html(parse(SRC), "base", "dev")
        assert "No differences between" in html

    def test_non_base_pair_compares_both_overlays(self):
        # Overlay-vs-overlay proves the environments list survives the
        # overlay-application quirk for both sides of the comparison.
        html = generate_compare_html(parse(SRC), "dev", "prod")
        assert 'class="cmp-changed"' in html
        assert "replicas" in html

    def test_unknown_env_a_raises(self):
        with pytest.raises(EnvironmentNotFoundError):
            generate_compare_html(parse(SRC), "nope", "prod")

    def test_unknown_env_b_raises(self):
        with pytest.raises(EnvironmentNotFoundError):
            generate_compare_html(parse(SRC), "base", "nope")

    def test_single_file_offline_document(self):
        html = generate_compare_html(parse(SRC), "base", "prod")
        assert html.startswith("<!DOCTYPE html>")
        assert "http://" not in html and "https://" not in html


# --------------------------------------------------------------------------- #
# Diff machinery — added/removed rows (unreachable via overlays today: the
# DSL only overrides existing services, but the diff must stay symmetric).
# --------------------------------------------------------------------------- #


def _meta(kind: str = "service", image: str = "img:1", replicas: int = 1):
    return {
        "kind": kind,
        "image": image,
        "replicas": replicas,
        "ports": "",
        "expose": "false",
        "env": {},
        "resources": {},
        "storage": "",
    }


class TestDiffWorkloads:
    def test_added_service_row(self):
        from infra.analyzer.ui_generator import _diff_workloads

        rows = _diff_workloads({"a": _meta()}, {"a": _meta(), "b": _meta()})
        added = [r for r in rows if r[0] == "added"]
        assert len(added) == 1 and added[0][1] == "b"
        assert added[0][3] == "" and "service · img:1" in added[0][4]

    def test_removed_service_row(self):
        from infra.analyzer.ui_generator import _diff_workloads

        rows = _diff_workloads({"a": _meta(), "b": _meta()}, {"a": _meta()})
        removed = [r for r in rows if r[0] == "removed"]
        assert len(removed) == 1 and removed[0][1] == "b"
        assert "service · img:1" in removed[0][3] and removed[0][4] == ""

    def test_rendered_badges(self):
        from infra.analyzer.ui_generator import (
            _compare_summary_html,
            _diff_workloads,
        )

        rows = _diff_workloads({"a": _meta(), "b": _meta()}, {"a": _meta()})
        html = _compare_summary_html(rows, "base", "prod")
        assert 'class="cmp-removed"' in html

    def test_changed_env_and_resources_rows(self):
        from infra.analyzer.ui_generator import _diff_workloads

        wa = {"s": {**_meta(), "env": {"X": "1"}, "resources": {"limits.cpu": "250m"}}}
        wb = {"s": {**_meta(), "env": {"X": "2"}, "resources": {"limits.cpu": "500m"}}}
        rows = _diff_workloads(wa, wb)
        fields = {r[2] for r in rows}
        assert "env.X" in fields and "resources.limits.cpu" in fields


# --------------------------------------------------------------------------- #
# Helpers — expression flattening, env-var text, summary flattening
# --------------------------------------------------------------------------- #


class TestExprAndEnvText:
    def test_expr_text_variants(self):
        from infra.analyzer.ui_generator import _expr_text
        from infra.parser import ast_nodes as n

        assert _expr_text(None) == ""
        assert _expr_text(n.Literal(42)) == "42"
        assert _expr_text(n.Identifier("region")) == "region"
        tpl = n.TemplateString(parts=("eu-", n.Identifier("zone"), "-1"))
        assert _expr_text(tpl) == "eu-…-1"
        assert _expr_text(object()).startswith("<")

    def test_env_var_text_sources(self):
        from infra.analyzer.ui_generator import _env_var_text
        from infra.parser import ast_nodes as n

        assert _env_var_text(n.EnvEntry("A", from_secret="s.k")) == "from secret s.k"
        assert _env_var_text(n.EnvEntry("A", from_config="c.k")) == (
            "from configmap c.k"
        )
        assert _env_var_text(n.EnvEntry("A", from_field="spec.x")) == (
            "from field spec.x"
        )
        assert _env_var_text(n.EnvEntry("A", from_env="HOSTVAR")) == (
            "from env HOSTVAR"
        )
        assert _env_var_text(n.EnvEntry("A")) == ""


class TestWorkloadSummary:
    def test_resources_partial_sections(self):
        from infra.analyzer.ui_generator import _workload_summary

        prog = parse(
            'service a { image: "i:1" '
            "resources { requests: {memory: 256Mi} } }\n"
            'service b { image: "i:1" '
            "resources { limits: {cpu: 500m} } }\n"
            'service c { image: "i:1" }\n'
        )
        summary = _workload_summary(prog)
        assert summary["a"]["resources"] == {"requests.memory": "256Mi"}
        assert summary["b"]["resources"] == {"limits.cpu": "500m"}
        assert summary["c"]["resources"] == {}

    def test_database_without_storage(self):
        from infra.analyzer.ui_generator import _workload_summary

        prog = parse("database d { type: postgres }\n")
        assert _workload_summary(prog)["d"]["storage"] == ""

    def test_cache_and_queue_summaries(self):
        from infra.analyzer.ui_generator import _workload_summary

        prog = parse("cache c { type: redis }\nqueue q { type: rabbitmq }\n")
        summary = _workload_summary(prog)
        assert summary["c"]["kind"] == "cache"
        assert summary["c"]["image"].startswith("redis:")
        assert summary["q"]["kind"] == "queue"

    def test_workload_line_with_storage(self):
        from infra.analyzer.ui_generator import _workload_line

        meta = {
            "kind": "database",
            "image": "postgres:16",
            "replicas": 1,
            "storage": "10Gi",
        }
        assert _workload_line(meta) == ("database · postgres:16 · replicas: 1 · 10Gi")

    def test_empty_panel_state(self):
        html = generate_compare_html(parse("# nothing declared\n"), "base", "base")
        assert "(no workloads)" in html
        assert "No differences between" in html


class TestStandaloneSvgInternals:
    def test_unknown_edge_endpoints_are_skipped(self):
        from infra.analyzer.ui_generator import _dag_svg_standalone

        svg = _dag_svg_standalone([], [("ghost-a", "ghost-b")])
        assert "data-from" not in svg
        assert "No workloads declared." in svg


# --------------------------------------------------------------------------- #
# CLI — `infra ui/serve --compare`
# --------------------------------------------------------------------------- #


class TestCompareCLI:
    def test_export_writes_file(self, tmp_path):
        f = _write(tmp_path)
        out = tmp_path / "cmp.html"
        r = runner.invoke(
            app, ["ui", str(f), "--compare", "base", "prod", "-o", str(out)]
        )
        assert r.exit_code == 0, r.output
        assert "Compare report written:" in r.output
        body = out.read_text(encoding="utf-8")
        assert 'class="cmp-panel"' in body

    def test_export_alias_serve(self, tmp_path):
        f = _write(tmp_path)
        out = tmp_path / "cmp.html"
        r = runner.invoke(
            app, ["serve", str(f), "--compare", "base", "prod", "-o", str(out)]
        )
        assert r.exit_code == 0, r.output
        assert (tmp_path / "cmp.html").exists()

    def test_unknown_env_exit_1(self, tmp_path):
        f = _write(tmp_path)
        out = tmp_path / "cmp.html"
        r = runner.invoke(
            app, ["ui", str(f), "--compare", "base", "nope", "-o", str(out)]
        )
        assert r.exit_code == 1
        assert "Environment 'nope' is not defined" in r.output
        assert not out.exists()

    def test_broken_file_exit_1(self, tmp_path):
        f = _write(tmp_path, "service { {\n")
        r = runner.invoke(app, ["ui", str(f), "--compare", "base", "prod"])
        assert r.exit_code == 1

    def test_missing_file_exit_1(self, tmp_path):
        r = runner.invoke(
            app,
            ["ui", str(tmp_path / "nope.infra"), "--compare", "base", "prod"],
        )
        assert r.exit_code == 1

    def test_compare_conflicts_with_environment_flag(self, tmp_path):
        f = _write(tmp_path)
        r = runner.invoke(
            app,
            ["ui", str(f), "--compare", "base", "prod", "-e", "prod"],
        )
        assert r.exit_code == 1
        assert "cannot be combined" in r.output

    def test_identical_envs_still_succeed(self, tmp_path):
        f = _write(tmp_path)
        out = tmp_path / "same.html"
        r = runner.invoke(
            app, ["ui", str(f), "--compare", "prod", "prod", "-o", str(out)]
        )
        assert r.exit_code == 0, r.output
        assert "No differences between" in out.read_text(encoding="utf-8")


class TestCompareLiveServe:
    def test_live_compare_serves_snapshot_page(self, tmp_path, monkeypatch):
        from infra.cli.serve_cmd import _DashboardHTTPServer

        def _interrupt(self, poll_interval=0.5):
            raise KeyboardInterrupt

        monkeypatch.setattr(_DashboardHTTPServer, "serve_forever", _interrupt)
        f = _write(tmp_path)
        r = runner.invoke(
            app,
            [
                "ui",
                str(f),
                "--compare",
                "base",
                "prod",
                "--port",
                "0",
                "--no-browser",
            ],
        )
        assert r.exit_code == 0, r.output
        assert "Compare report ready: http://localhost:" in r.output
        assert "[SKIP] Browser auto-open disabled" in r.output
        assert "[OK] Server stopped." in r.output


class TestCompareLiveServer:
    def test_served_page_is_the_compare_report(self, tmp_path):
        from infra.cli.serve_cmd import make_server, render_compare

        f = _write(tmp_path)
        server = make_server(f, 0, static_html=render_compare(f, "base", "prod"))
        try:
            assert server.server_port > 0
            body = server.RequestHandlerClass.render_fn()
            assert 'class="cmp-panel"' in body
            assert "Diff summary" in body
        finally:
            server.server_close()

    def test_make_server_static_html_requires_no_reparse(self, tmp_path):
        from infra.cli.serve_cmd import make_server

        f = _write(tmp_path)
        f.unlink()  # static page must not depend on the file anymore
        server = make_server(f, 0, static_html="<html>snapshot</html>")
        try:
            page = server.RequestHandlerClass.render_fn()
            assert page == "<html>snapshot</html>"
        finally:
            server.server_close()
