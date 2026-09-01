"""Tests for `infra alert` — Slack/Teams/Discord webhooks (v0.7.0).

All HTTP is mocked (urllib.request.urlopen); no real webhook URLs are ever
contacted or printed unmasked.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest
from typer.testing import CliRunner

from infra.alerts.webhooks import (
    ALL_EVENTS,
    EVENT_COST_EXCEEDED,
    EVENT_DRIFT,
    EVENT_SECURITY,
    AlertConfigError,
    AlertContext,
    AlertEvent,
    WebhookTarget,
    build_payload,
    evaluate_alerts,
    load_alert_config,
    mask_url,
    post_webhook,
)
from infra.analyzer.drift import DriftItem, DriftReport
from infra.cli.main import app
from infra.parser import parse

runner = CliRunner()

COSTLY = (
    'service api {\n    image: "myapp:1.0"\n    replicas: 8\n}\n'
    'database db {\n    type: "postgres"\n}\n'
)

INSECURE = (
    "service api {\n"
    '    image: "myapp:1.0"\n'
    "    env {\n"
    '        PASSWORD: "hardcoded123"\n'
    "    }\n"
    "}\n"
)

CLEAN = 'service api {\n    image: "myapp:1.0"\n}\n'


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen_ok(monkeypatch, collector=None):
    def fake_urlopen(request, timeout=None):
        if collector is not None:
            collector.append((request, timeout))
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


class TestEvaluateAlerts:
    def test_cost_exceeded_event(self):
        ctx = evaluate_alerts(parse(COSTLY), source="app.infra", max_monthly_cost=1.0)
        assert ctx.triggered
        assert ctx.events[0].event_type == EVENT_COST_EXCEEDED
        assert "exceeds" in ctx.events[0].lines[0]

    def test_cost_within_limit_no_event(self):
        ctx = evaluate_alerts(parse(CLEAN), source="a", max_monthly_cost=10**9)
        assert not ctx.triggered

    def test_no_limit_no_cost_event(self):
        ctx = evaluate_alerts(parse(COSTLY), source="a")
        assert all(e.event_type != EVENT_COST_EXCEEDED for e in ctx.events)

    def test_security_violation_event(self):
        ctx = evaluate_alerts(parse(INSECURE), source="a")
        assert any(e.event_type == EVENT_SECURITY for e in ctx.events)
        sec = next(e for e in ctx.events if e.event_type == EVENT_SECURITY)
        assert any("SEC001" in line for line in sec.lines)

    def test_security_warnings_do_not_trigger(self):
        # nginx:latest -> SEC003 warning only, never pages the team.
        ctx = evaluate_alerts(parse('service a { image: "nginx:latest" }'), source="a")
        assert all(e.event_type != EVENT_SECURITY for e in ctx.events)

    def test_drift_event_from_report(self):
        report = DriftReport(
            target="k8s",
            items=[
                DriftItem(
                    resource="api",
                    parameter="replicas",
                    expected="3",
                    live="1",
                )
            ],
        )
        ctx = evaluate_alerts(parse(COSTLY), source="a", drift_report=report)
        assert any(e.event_type == EVENT_DRIFT for e in ctx.events)

    def test_drift_report_without_drift_no_event(self):
        report = DriftReport(target="k8s", in_sync=["api"])
        ctx = evaluate_alerts(parse(CLEAN), source="a", drift_report=report)
        assert not ctx.triggered

    def test_events_filter_narrows_evaluation(self):
        report = DriftReport(
            target="k8s",
            items=[DriftItem(resource="a", parameter="p", expected="1", live="2")],
        )
        ctx = evaluate_alerts(
            parse(INSECURE),
            source="a",
            drift_report=report,
            events=[EVENT_DRIFT],
        )
        assert [e.event_type for e in ctx.events] == [EVENT_DRIFT]

    def test_all_events_constant(self):
        assert set(ALL_EVENTS) == {
            EVENT_DRIFT,
            EVENT_COST_EXCEEDED,
            EVENT_SECURITY,
        }

    def test_context_to_dict(self):
        ctx = evaluate_alerts(parse(CLEAN), source="x", max_monthly_cost=10**9)
        data = ctx.to_dict()
        assert data["source"] == "x" and data["events"] == []


class TestPayloads:
    def _ctx(self, triggered=True):
        events = (
            [AlertEvent(EVENT_SECURITY, "Security violations (1)", ["SEC001: x"])]
            if triggered
            else []
        )
        return AlertContext(
            source="app.infra",
            monthly_usd=42.5,
            max_monthly_cost=10.0,
            events=events,
        )

    def test_slack_payload_structure(self):
        payload = build_payload("slack", self._ctx())
        assert "Infra Lang" in payload["text"]
        assert payload["blocks"][0]["type"] == "header"
        flat = json.dumps(payload)
        assert "app.infra" in flat and "$42.50" in flat and "SEC001" in flat

    def test_slack_all_clear(self):
        payload = build_payload("slack", self._ctx(triggered=False))
        assert "All checks green" in json.dumps(payload)

    def test_teams_payload_structure(self):
        payload = build_payload("teams", self._ctx())
        assert payload["@type"] == "MessageCard"
        assert payload["themeColor"] == "D63301"
        assert any(
            s.get("title") == "Security violations (1)" for s in payload["sections"]
        )

    def test_teams_all_clear_green_theme(self):
        payload = build_payload("teams", self._ctx(triggered=False))
        assert payload["themeColor"] == "2EB886"

    def test_discord_payload_structure(self):
        payload = build_payload("discord", self._ctx())
        embed = payload["embeds"][0]
        assert embed["color"] == 0xD63301
        assert "`app.infra`" in embed["description"]
        assert any(f["name"] == "Monthly cost" for f in embed["fields"])

    def test_discord_all_clear(self):
        payload = build_payload("discord", self._ctx(triggered=False))
        embed = payload["embeds"][0]
        assert embed["color"] == 0x2EB886
        assert "All checks green" in json.dumps(payload)

    def test_unknown_format_raises(self):
        with pytest.raises(ValueError, match="unknown webhook format"):
            build_payload("pagerduty", self._ctx())


class TestMaskUrl:
    def test_masks_path_and_query(self):
        assert (
            mask_url("https://hooks.slack.com/services/T00/B00/SECRET")
            == "https://hooks.slack.com/***"
        )

    def test_garbage_url(self):
        assert mask_url("not a url") == "***"

    def test_empty_url(self):
        assert mask_url("") == "***"


class TestPostWebhook:
    def test_success(self, monkeypatch):
        calls = []
        _patch_urlopen_ok(monkeypatch, calls)
        ok, detail = post_webhook("https://hooks.example.com/x", {"a": 1})
        assert ok and detail == "HTTP 200"
        request, timeout = calls[0]
        assert timeout == 10.0
        assert request.headers["Content-type"] == "application/json"
        assert "infra-lang/" in request.headers["User-agent"]

    def test_custom_timeout(self, monkeypatch):
        calls = []
        _patch_urlopen_ok(monkeypatch, calls)
        post_webhook("https://hooks.example.com/x", {}, timeout=2.5)
        assert calls[0][1] == 2.5

    def test_http_error(self, monkeypatch):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                "https://x",
                500,
                "server",
                {},
                None,  # type: ignore[arg-type]
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        ok, detail = post_webhook("https://hooks.example.com/x", {})
        assert not ok and detail == "HTTP 500"

    def test_url_error(self, monkeypatch):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        ok, detail = post_webhook("https://hooks.example.com/x", {})
        assert not ok and "refused" in detail

    def test_never_raises_on_os_error(self, monkeypatch):
        def fake_urlopen(request, timeout=None):
            raise OSError("network down")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        ok, _ = post_webhook("https://hooks.example.com/x", {})
        assert not ok


class TestConfigFile:
    def test_valid_config(self, tmp_path):
        cfg = _write(
            tmp_path,
            ".infra-alert.yml",
            "max_monthly_cost: 500\n"
            "webhooks:\n"
            "  - url: https://hooks.slack.com/services/AAA\n"
            "    format: slack\n"
            "    events: [drift, cost_exceeded]\n"
            "  - url: https://discord.com/api/webhooks/BBB\n"
            "    format: discord\n",
        )
        config = load_alert_config(cfg)
        assert config.max_monthly_cost == 500.0
        assert len(config.webhooks) == 2
        assert config.webhooks[0].events == ["drift", "cost_exceeded"]
        assert config.webhooks[1].events is None
        assert config.webhooks[1].format == "discord"

    def test_bad_yaml(self, tmp_path):
        cfg = _write(tmp_path, ".infra-alert.yml", ":\n  - [")
        with pytest.raises(AlertConfigError, match="cannot parse YAML"):
            load_alert_config(cfg)

    def test_non_mapping_top_level(self, tmp_path):
        cfg = _write(tmp_path, ".infra-alert.yml", "- just\n- a\n- list\n")
        with pytest.raises(AlertConfigError, match="expected a mapping"):
            load_alert_config(cfg)

    def test_bad_max_cost(self, tmp_path):
        cfg = _write(tmp_path, ".infra-alert.yml", "max_monthly_cost: soon\n")
        with pytest.raises(AlertConfigError, match="must be a number"):
            load_alert_config(cfg)

    def test_webhooks_not_a_list(self, tmp_path):
        cfg = _write(tmp_path, ".infra-alert.yml", "webhooks: nope\n")
        with pytest.raises(AlertConfigError, match="must be a list"):
            load_alert_config(cfg)

    def test_webhook_entry_not_mapping(self, tmp_path):
        cfg = _write(tmp_path, ".infra-alert.yml", "webhooks: [42]\n")
        with pytest.raises(AlertConfigError, match="must be a mapping"):
            load_alert_config(cfg)

    def test_webhook_missing_url(self, tmp_path):
        cfg = _write(tmp_path, ".infra-alert.yml", "webhooks:\n  - format: slack\n")
        with pytest.raises(AlertConfigError, match="'url' is required"):
            load_alert_config(cfg)

    def test_webhook_unknown_format(self, tmp_path):
        cfg = _write(
            tmp_path,
            ".infra-alert.yml",
            "webhooks:\n  - url: http://x\n    format: icq\n",
        )
        with pytest.raises(AlertConfigError, match="unknown format"):
            load_alert_config(cfg)

    def test_webhook_unknown_event(self, tmp_path):
        cfg = _write(
            tmp_path,
            ".infra-alert.yml",
            "webhooks:\n  - url: http://x\n    events: [explosion]\n",
        )
        with pytest.raises(AlertConfigError, match="unknown event"):
            load_alert_config(cfg)

    def test_webhook_events_not_a_list(self, tmp_path):
        cfg = _write(
            tmp_path,
            ".infra-alert.yml",
            "webhooks:\n  - url: http://x\n    events: drift\n",
        )
        with pytest.raises(AlertConfigError, match="must be a list of strings"):
            load_alert_config(cfg)

    def test_target_accepts(self):
        t = WebhookTarget(url="http://x", format="slack", events=[EVENT_DRIFT])
        assert t.accepts(AlertEvent(EVENT_DRIFT, "d"))
        assert not t.accepts(AlertEvent(EVENT_SECURITY, "s"))
        assert WebhookTarget(url="http://x", format="slack").accepts(
            AlertEvent(EVENT_SECURITY, "s")
        )


class TestAlertCLI:
    def test_dry_run_slack(self, tmp_path):
        f = _write(tmp_path, "app.infra", INSECURE)
        result = runner.invoke(
            app,
            [
                "alert",
                str(f),
                "--webhook",
                "https://hooks.slack.com/services/SECRET",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "DRY-RUN" in result.output
        assert '"blocks"' in result.output
        assert "SECURITY" in result.output.upper()
        assert "SECRET" not in result.output  # URL is masked everywhere

    def test_skip_when_nothing_triggered(self, tmp_path):
        f = _write(tmp_path, "app.infra", CLEAN)
        result = runner.invoke(
            app, ["alert", str(f), "--webhook", "https://x.example/hook"]
        )
        assert result.exit_code == 0
        assert "SKIP" in result.output

    def test_always_sends_all_clear(self, tmp_path, monkeypatch):
        calls = []
        _patch_urlopen_ok(monkeypatch, calls)
        f = _write(tmp_path, "app.infra", CLEAN)
        result = runner.invoke(
            app, ["alert", str(f), "--webhook", "https://x.example/hook", "--always"]
        )
        assert result.exit_code == 0
        assert "[OK]" in result.output
        assert len(calls) == 1

    def test_delivery_failure_exits_1(self, tmp_path, monkeypatch):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError("boom")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        f = _write(tmp_path, "app.infra", INSECURE)
        result = runner.invoke(
            app, ["alert", str(f), "--webhook", "https://x.example/hook"]
        )
        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "boom" in result.output

    def test_delivery_masked_url_on_success(self, tmp_path, monkeypatch):
        _patch_urlopen_ok(monkeypatch)
        f = _write(tmp_path, "app.infra", INSECURE)
        result = runner.invoke(
            app,
            ["alert", str(f), "--webhook", "https://hooks.slack.com/TOP/SECRET"],
        )
        assert result.exit_code == 0
        assert "SECRET" not in result.output
        assert "hooks.slack.com/***" in result.output

    def test_config_file_targets(self, tmp_path, monkeypatch):
        calls = []
        _patch_urlopen_ok(monkeypatch, calls)
        _write(
            tmp_path,
            ".infra-alert.yml",
            "max_monthly_cost: 0.01\n"
            "webhooks:\n"
            "  - url: https://hooks.slack.com/services/AAA\n"
            "    format: slack\n"
            "    events: [cost_exceeded]\n"
            "  - url: https://discord.com/api/webhooks/BBB\n"
            "    format: discord\n"
            "    events: [drift]\n",
        )
        f = _write(tmp_path, "app.infra", COSTLY)
        result = runner.invoke(
            app, ["alert", str(f), "-c", str(tmp_path / ".infra-alert.yml")]
        )
        assert result.exit_code == 0
        # Only the slack target subscribed to cost_exceeded gets a POST;
        # the discord (drift-only) one is skipped.
        assert len(calls) == 1
        assert "SKIP" in result.output

    def test_config_max_cost_used_when_cli_absent(self, tmp_path):
        _write(
            tmp_path,
            ".infra-alert.yml",
            "max_monthly_cost: 0.01\nwebhooks:\n  - url: https://x\n",
        )
        f = _write(tmp_path, "app.infra", COSTLY)
        result = runner.invoke(
            app,
            [
                "alert",
                str(f),
                "-c",
                str(tmp_path / ".infra-alert.yml"),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "cost" in result.output.lower()

    def test_cli_max_cost_overrides_config(self, tmp_path):
        _write(
            tmp_path,
            ".infra-alert.yml",
            "max_monthly_cost: 999999\nwebhooks:\n  - url: https://x\n",
        )
        f = _write(tmp_path, "app.infra", COSTLY)
        ok = runner.invoke(
            app, ["alert", str(f), "-c", str(tmp_path / ".infra-alert.yml")]
        )
        assert ok.exit_code == 0
        assert "SKIP" in ok.output  # high config limit -> nothing triggered
        fired = runner.invoke(
            app,
            [
                "alert",
                str(f),
                "-c",
                str(tmp_path / ".infra-alert.yml"),
                "--max-monthly-cost",
                "0.01",
                "--dry-run",
            ],
        )
        assert '"blocks"' in fired.output

    def test_events_option_filter(self, tmp_path):
        f = _write(tmp_path, "app.infra", INSECURE)
        result = runner.invoke(
            app,
            [
                "alert",
                str(f),
                "--webhook",
                "https://x",
                "--events",
                "drift",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "SKIP" in result.output  # security event filtered out

    def test_events_option_unknown_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", CLEAN)
        result = runner.invoke(
            app, ["alert", str(f), "--webhook", "https://x", "--events", "nope"]
        )
        assert result.exit_code == 1

    def test_no_targets_exits_2(self, tmp_path):
        f = _write(tmp_path, "app.infra", CLEAN)
        result = runner.invoke(app, ["alert", str(f)])
        assert result.exit_code == 2

    def test_missing_file_exits_1(self, tmp_path):
        result = runner.invoke(
            app, ["alert", str(tmp_path / "nope.infra"), "--webhook", "https://x"]
        )
        assert result.exit_code == 1

    def test_missing_config_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", CLEAN)
        result = runner.invoke(
            app,
            ["alert", str(f), "-c", str(tmp_path / "nope.yml")],
        )
        assert result.exit_code == 1

    def test_invalid_config_exits_1(self, tmp_path):
        cfg = _write(tmp_path, ".infra-alert.yml", "webhooks: [{}]\n")
        f = _write(tmp_path, "app.infra", CLEAN)
        result = runner.invoke(app, ["alert", str(f), "-c", str(cfg)])
        assert result.exit_code == 1

    def test_unknown_format_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", CLEAN)
        result = runner.invoke(
            app, ["alert", str(f), "--webhook", "https://x", "-f", "icq"]
        )
        assert result.exit_code == 1

    def test_live_drift_probe_error_is_skip_not_crash(self, tmp_path, monkeypatch):
        from infra.cli import alert_cmd

        monkeypatch.setattr(
            alert_cmd,
            "_probe_drift_safely",
            lambda program, target, namespace: DriftReport(
                target=target, error="kubectl not found"
            ),
        )
        f = _write(tmp_path, "app.infra", CLEAN)
        result = runner.invoke(
            app,
            [
                "alert",
                str(f),
                "--webhook",
                "https://x",
                "--live-drift",
                "--dry-run",
                "--always",
            ],
        )
        assert result.exit_code == 0
        assert "kubectl not found" in result.output

    def test_live_drift_triggers_event(self, tmp_path, monkeypatch):
        from infra.cli import alert_cmd

        report = DriftReport(
            target="k8s",
            items=[
                DriftItem(resource="api", parameter="replicas", expected="3", live="1")
            ],
        )
        monkeypatch.setattr(
            alert_cmd,
            "_probe_drift_safely",
            lambda program, target, namespace: report,
        )
        f = _write(tmp_path, "app.infra", CLEAN)
        result = runner.invoke(
            app,
            [
                "alert",
                str(f),
                "--webhook",
                "https://x",
                "--live-drift",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "drift" in result.output.lower()

    def test_probe_exception_converted_to_report_error(self, monkeypatch):
        from infra.cli import alert_cmd

        def boom(program, target, namespace):
            raise RuntimeError("cluster exploded")

        monkeypatch.setattr("infra.analyzer.drift.detect_live_drift_program", boom)
        report = alert_cmd._probe_drift_safely(parse(CLEAN), "k8s", "default")
        assert report.error is not None
        assert "exploded" in report.error

    def test_teams_format_dry_run(self, tmp_path):
        f = _write(tmp_path, "app.infra", INSECURE)
        result = runner.invoke(
            app,
            [
                "alert",
                str(f),
                "--webhook",
                "https://office.example/webhook",
                "-f",
                "teams",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "MessageCard" in result.output

    def test_help(self):
        result = runner.invoke(app, ["alert", "--help"])
        assert result.exit_code == 0
        assert "webhook" in result.output
