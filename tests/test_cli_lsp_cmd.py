"""Tests for the ``infra lsp`` server-start paths (v0.5.3 coverage push).

Covers the previously-uncovered branches of ``src/infra/cli/lsp_cmd.py``:
the pygls-missing ``ImportError`` exit path and both server start modes
(stdio / TCP), with pygls fully mocked out so tests stay hermetic and fast.
"""

from __future__ import annotations

import sys
from unittest import mock

from typer.testing import CliRunner

from infra.cli.main import app

runner = CliRunner()


class TestLspServerStart:
    def test_start_io_default(self) -> None:
        fake_server = mock.Mock()
        fake_module = mock.Mock(server=fake_server)
        with mock.patch.dict(sys.modules, {"infra.lsp.server": fake_module}):
            result = runner.invoke(app, ["lsp"])
        assert result.exit_code == 0
        fake_server.start_io.assert_called_once_with()
        fake_server.start_tcp.assert_not_called()

    def test_start_tcp_with_host_and_port(self) -> None:
        fake_server = mock.Mock()
        fake_module = mock.Mock(server=fake_server)
        with mock.patch.dict(sys.modules, {"infra.lsp.server": fake_module}):
            result = runner.invoke(
                app, ["lsp", "--tcp", "--host", "0.0.0.0", "--port", "9999"]
            )
        assert result.exit_code == 0
        fake_server.start_tcp.assert_called_once_with("0.0.0.0", 9999)
        fake_server.start_io.assert_not_called()

    def test_pygls_missing_exits_with_hint(self) -> None:
        # ``sys.modules[name] = None`` makes the next import of that module
        # raise ImportError — this simulates a pygls-less installation.
        with mock.patch.dict(sys.modules, {"infra.lsp.server": None}):
            result = runner.invoke(app, ["lsp"])
        assert result.exit_code == 1
        assert "pygls not installed" in result.output
        assert "infra-lang[lsp]" in result.output
