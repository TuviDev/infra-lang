"""Tests for `infra ui --publish` — static team dashboard export (v0.7.0)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from infra.analyzer.drift import DriftItem, DriftReport
from infra.cli.main import app
from infra.cli.serve_cmd import publish_site
from infra.version import __version__

runner = CliRunner()

APP = (
    "service frontend {\n"
    '    image: "nginx:1.25"\n'
    "    depends_on: [api]\n"
    "}\n"
    "service api {\n"
    '    image: "myapp:1.0"\n'
    "    port: 9000\n"
    "    replicas: 2\n"
    "    depends_on: [db]\n"
    "}\n"
    "database db {\n"
    '    type: "postgres"\n'
    "}\n"
    'environment "prod" {\n'
    "    service api {\n"
    "        replicas: 5\n"
    "    }\n"
    "}\n"
    'environment "staging eu" {\n'
    "    service api {\n"
    "        replicas: 1\n"
    "    }\n"
    "}\n"
)

NO_ENVS = 'service api {\n    image: "myapp:1.0"\n}\n'


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


class TestPublishSite:
    def test_creates_expected_layout(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        out = tmp_path / "site"
        written = publish_site(f, out, None)
        assert (out / "index.html").exists()
        assert (out / "data" / "summary.json").exists()
        assert (out / "data" / "history" / "index.json").exists()
        assert written["snapshot"].exists()
        assert set(written["env_pages"]) == {"prod", "staging eu"}
        assert (out / written["env_pages"]["prod"]).exists()

    def test_env_page_filename_sanitized(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        out = tmp_path / "site"
        written = publish_site(f, out, None)
        # "staging eu" -> page name must be a plain filename, never a path
        page = written["env_pages"]["staging eu"]
        assert page == "envs/staging-eu.html"
        assert (out / page).exists()
        html = (out / "envs" / "prod.html").read_text(encoding="utf-8")
        assert "prod" in html

    def test_summary_json_content(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        out = tmp_path / "site"
        publish_site(f, out, None)
        summary = json.loads(
            (out / "data" / "summary.json").read_text(encoding="utf-8")
        )
        assert summary["tool"] == "infra-lang"
        assert summary["version"] == __version__
        assert summary["currency"] == "USD"
        assert summary["monthly_usd"] > 0
        assert {r["name"] for r in summary["resources"]} == {
            "frontend",
            "api",
            "db",
        }
        assert summary["environments"] == ["prod", "staging eu"]
        assert summary["drift"] is None
        assert summary["pages"]["index"] == "index.html"

    def test_index_html_looks_like_dashboard(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        out = tmp_path / "site"
        publish_site(f, out, None)
        html = (out / "index.html").read_text(encoding="utf-8")
        assert "<html" in html.lower()
        assert "api" in html

    def test_history_appends_on_rerun(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        out = tmp_path / "site"
        first = publish_site(f, out, None)
        second = publish_site(f, out, None)
        assert first["history_count"] == 1
        assert second["history_count"] == 2
        index = json.loads(
            (out / "data" / "history" / "index.json").read_text(encoding="utf-8")
        )
        assert len(index) == 2
        assert all("monthly_usd" in entry for entry in index)
        snapshots = list((out / "data" / "history").glob("*.json"))
        # index.json + 2 timestamped snapshots
        assert len(snapshots) == 3

    def test_history_index_corruption_recovered(self, tmp_path):
        f = _write(tmp_path, "app.infra", NO_ENVS)
        out = tmp_path / "site"
        history = out / "data" / "history"
        history.mkdir(parents=True)
        (history / "index.json").write_text("{not json", encoding="utf-8")
        written = publish_site(f, out, None)
        assert written["history_count"] == 1

    def test_no_environments_no_envs_dir(self, tmp_path):
        f = _write(tmp_path, "app.infra", NO_ENVS)
        out = tmp_path / "site"
        written = publish_site(f, out, None)
        assert written["env_pages"] == {}
        assert not (out / "envs").exists()

    def test_selected_environment_index(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        out = tmp_path / "site"
        publish_site(f, out, "prod")
        summary = json.loads(
            (out / "data" / "summary.json").read_text(encoding="utf-8")
        )
        assert summary["environment"] == "prod"
        base = publish_site(f, tmp_path / "base-site", None)
        base_summary = json.loads(base["summary"].read_text(encoding="utf-8"))
        assert summary["monthly_usd"] > base_summary["monthly_usd"]

    def test_live_drift_snapshot(self, tmp_path, monkeypatch):
        from infra.cli import serve_cmd

        report = DriftReport(
            target="k8s",
            items=[
                DriftItem(resource="api", parameter="replicas", expected="2", live="1")
            ],
        )
        monkeypatch.setattr(
            serve_cmd,
            "_probe_drift_safely",
            lambda program, target, namespace: report,
        )
        f = _write(tmp_path, "app.infra", NO_ENVS)
        out = tmp_path / "site"
        publish_site(f, out, None, live_drift=True)
        summary = json.loads(
            (out / "data" / "summary.json").read_text(encoding="utf-8")
        )
        assert summary["drift"] is not None
        assert summary["drift"]["drift"]
        assert summary["drift"]["has_drift"] is True
        index = json.loads(
            (out / "data" / "history" / "index.json").read_text(encoding="utf-8")
        )
        assert index[0]["drift"] is True

    def test_page_name_fallback(self):
        from infra.cli.serve_cmd import _page_name

        assert _page_name("prod") == "prod"
        assert _page_name("weird/../name") == "weird-name"
        assert _page_name("...") == "env"


class TestPublishCLI:
    def test_publish_via_ui_alias(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        out = tmp_path / "site"
        result = runner.invoke(app, ["ui", str(f), "--publish", str(out)])
        assert result.exit_code == 0
        assert "[OK] Static dashboard published" in result.output
        assert "environments (2)" in result.output
        assert (out / "index.html").exists()

    def test_publish_via_serve(self, tmp_path):
        f = _write(tmp_path, "app.infra", NO_ENVS)
        out = tmp_path / "site"
        result = runner.invoke(app, ["serve", str(f), "--publish", str(out)])
        assert result.exit_code == 0
        assert "history snapshot #1" in result.output
        # No HTTP server was started, nothing about ports/browsers:
        assert "Ctrl+C" not in result.output

    def test_publish_and_compare_conflict(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        result = runner.invoke(
            app,
            [
                "ui",
                str(f),
                "--publish",
                str(tmp_path / "s"),
                "--compare",
                "base",
                "prod",
            ],
        )
        assert result.exit_code == 1

    def test_publish_and_output_html_conflict(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        result = runner.invoke(
            app,
            [
                "ui",
                str(f),
                "--publish",
                str(tmp_path / "s"),
                "-o",
                str(tmp_path / "x.html"),
            ],
        )
        assert result.exit_code == 1

    def test_publish_missing_file(self, tmp_path):
        result = runner.invoke(
            app, ["ui", str(tmp_path / "nope.infra"), "--publish", str(tmp_path)]
        )
        assert result.exit_code == 1

    def test_publish_parse_error(self, tmp_path):
        f = _write(tmp_path, "broken.infra", "service {{\n")
        result = runner.invoke(app, ["ui", str(f), "--publish", str(tmp_path / "s")])
        assert result.exit_code == 1

    def test_publish_creates_nested_directory(self, tmp_path):
        f = _write(tmp_path, "app.infra", NO_ENVS)
        out = tmp_path / "deep" / "nested" / "site"
        result = runner.invoke(app, ["ui", str(f), "--publish", str(out)])
        assert result.exit_code == 0
        assert (out / "index.html").exists()
