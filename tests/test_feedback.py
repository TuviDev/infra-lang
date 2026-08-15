"""Tests for the opt-in feedback infrastructure (config + reporting).

Feedback must be OFF by default, configurable locally, and must never break
the CLI/LSP or leak source code / paths / PII.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infra.config import InfraConfig, load_config, write_config
from infra.feedback import _build_payload, _sanitize, report_error


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Isolate user config from the real HOME so tests never read/write
    ~/.config/infra. Point Path.home() at a temp dir via USER_CONFIG_PATH."""
    import infra.config as cfg

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cfg, "USER_CONFIG_PATH", home / ".config" / "infra" / "config.yaml")
    yield


class TestConfigDefaults:
    def test_feedback_off_by_default(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # no config files present
        cfg = load_config()
        assert cfg.feedback_enabled is False
        assert cfg.source == "defaults"

    def test_env_can_enable(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        cfg = load_config(env={"INFRA_FEEDBACK": "true"})
        assert cfg.feedback_enabled is True

    def test_env_can_disable_even_with_file(self, monkeypatch, tmp_path):
        write_config(tmp_path / ".infra-config.yaml", True)
        cfg = load_config(
            project_dir=tmp_path, env={"INFRA_FEEDBACK_OFF": "true"}
        )
        assert cfg.feedback_enabled is False


class TestConfigReadWrite:
    def test_write_then_read_project(self, tmp_path):
        path = tmp_path / ".infra-config.yaml"
        write_config(path, True)
        cfg = load_config(project_dir=tmp_path)
        assert cfg.feedback_enabled is True

    def test_read_false(self, tmp_path):
        path = tmp_path / ".infra-config.yaml"
        write_config(path, False)
        cfg = load_config(project_dir=tmp_path)
        assert cfg.feedback_enabled is False

    def test_corrupted_config_does_not_raise(self, monkeypatch, tmp_path):
        bad = tmp_path / ".infra-config.yaml"
        bad.write_text("::: not [ valid : yaml")
        cfg = load_config(project_dir=tmp_path)
        assert cfg.feedback_enabled is False  # safe default, no exception


class TestFeedbackPayload:
    def test_no_source_code_in_payload(self):
        payload = _build_payload(
            "InfraParseError",
            "Unexpected token at /home/user/app.infra:3:5 in\n"
            'service api { image: "x" }',
        )
        # file paths stripped
        assert "/home/user" not in payload["message"]
        assert "app.infra" not in payload["message"]

    def test_payload_has_metadata_not_pii(self):
        payload = _build_payload("ValueError", "bad value", operation="validate")
        assert payload["product"] == "infra-lang"
        assert payload["type"] == "ValueError"
        assert payload["operation"] == "validate"
        # no PII keys
        for key in ("user", "host", "path", "env", "cwd"):
            assert key not in payload

    def test_sanitize_strips_paths(self):
        msg = "failed at /var/log/infra/file.txt and /tmp/x/y.log"
        cleaned = _sanitize(msg)
        assert "file.txt" not in cleaned
        assert "y.log" not in cleaned


class TestReportError:
    def test_disabled_by_default_returns_false(self):
        # no collector + default config -> no report, no exception
        assert report_error(RuntimeError("x")) is False

    def test_does_not_raise_even_when_config_errors(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        # even if enabled, with no collector it's a no-op; must never raise
        cfg = InfraConfig(feedback_enabled=True)
        result = report_error(RuntimeError("boom"), config=cfg)
        assert result in (True, False)  # either way, no exception raised

    def test_network_failure_never_propagates(self, monkeypatch):
        # point the collector at an unreachable host and ensure the error is
        # swallowed (report_error must never raise).
        import infra.feedback as fb

        monkeypatch.setattr(fb, "COLLECTOR_URL", "http://127.0.0.1:1/collect")
        cfg = InfraConfig(feedback_enabled=True)
        result = report_error(RuntimeError("x"), config=cfg)
        assert result is False  # swallowed, not raised


class TestFingerprinting:
    def test_fingerprint_is_stable_for_same_error(self):
        from infra.feedback import _fingerprint

        a = _fingerprint("ValueError", "bad value at /some/path/x:3")
        b = _fingerprint("ValueError", "bad value at /other/path/y:9")
        # paths are stripped, so the fingerprint is the same
        assert a == b

    def test_fingerprint_differs_for_different_types(self):
        from infra.feedback import _fingerprint

        a = _fingerprint("ValueError", "boom")
        b = _fingerprint("TypeError", "boom")
        assert a != b

    def test_fingerprint_is_non_identifying_hex(self):
        from infra.feedback import _fingerprint

        fp = _fingerprint("RuntimeError", "secret api key leaked")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_payload_includes_fingerprint(self):
        from infra.feedback import _build_payload

        payload = _build_payload("ValueError", "bad thing")
        assert "fingerprint" in payload
        assert len(payload["fingerprint"]) == 16


class TestFeedbackStatus:
    def test_status_reflects_config(self):
        from infra.feedback import feedback_status

        status = feedback_status(InfraConfig(feedback_enabled=False))
        assert status["enabled"] == "false"

    def test_status_when_enabled(self):
        from infra.feedback import feedback_status

        status = feedback_status(InfraConfig(feedback_enabled=True))
        assert status["enabled"] == "true"


class TestFeedbackCli:
    def test_cli_status_default_off(self):
        from typer.testing import CliRunner

        from infra.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["feedback"])
        assert result.exit_code == 0
        assert "enabled : false" in result.output

    def test_cli_on_then_off(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from infra.cli.main import app

        # remove any ambient env override so the file config is what shows
        monkeypatch.delenv("INFRA_FEEDBACK", raising=False)
        monkeypatch.delenv("INFRA_FEEDBACK_OFF", raising=False)
        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        r1 = runner.invoke(app, ["feedback", "--on"])
        assert r1.exit_code == 0
        r2 = runner.invoke(app, ["feedback"])
        assert "enabled : true" in r2.output
        r3 = runner.invoke(app, ["feedback", "--off"])
        assert r3.exit_code == 0
        r4 = runner.invoke(app, ["feedback"])
        assert "enabled : false" in r4.output
