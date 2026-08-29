"""Edge-case tests for infra.feedback network send & version fallback (v0.5.3)."""

from __future__ import annotations

import sys
from unittest import mock

import infra.feedback as fb
from infra.config import InfraConfig


class TestVersionFallback:
    def test_returns_unknown_when_version_module_missing(self) -> None:
        with mock.patch.dict(sys.modules, {"infra.version": None}):
            assert fb._version() == "unknown"


class TestReportErrorSendPaths:
    def _enabled(self) -> InfraConfig:
        return InfraConfig(feedback_enabled=True, source="config-file")

    def test_scheme_guard_rejects_file_url(self) -> None:
        cfg = self._enabled()
        with mock.patch.object(fb, "COLLECTOR_URL", "file:///etc/passwd"):
            assert fb.report_error(ValueError("boom"), config=cfg) is False

    def test_successful_send_returns_true(self) -> None:
        cfg = self._enabled()
        with (
            mock.patch.object(fb, "COLLECTOR_URL", "https://collector.example/x"),
            mock.patch("urllib.request.urlopen") as urlopen,
        ):
            ctx = mock.MagicMock()
            ctx.__enter__.return_value = ctx
            urlopen.return_value = ctx
            assert fb.report_error(ValueError("boom"), config=cfg) is True
            urlopen.assert_called_once()

    def test_collapsing_network_failure_returns_false(self) -> None:
        cfg = self._enabled()
        with (
            mock.patch.object(fb, "COLLECTOR_URL", "https://collector.example/x"),
            mock.patch("urllib.request.urlopen", side_effect=OSError("down")),
        ):
            assert fb.report_error(ValueError("boom"), config=cfg) is False

    def test_disabled_never_sends(self) -> None:
        cfg = InfraConfig(feedback_enabled=False, source="defaults")
        with mock.patch.object(fb, "COLLECTOR_URL", "https://collector.example/x"):
            assert fb.report_error(ValueError("boom"), config=cfg) is False
