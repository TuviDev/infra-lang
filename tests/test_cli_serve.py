"""Tests for ``infra serve`` / ``infra ui`` — local web dashboard (v0.5.2).

Covers the static ``--output-html`` export path (Typer CliRunner) and the
live HTTP path: a real loopback server on an OS-assigned free port serving
the regenerated dashboard, 404/500 handling, dynamic content refresh after
the file changes, port-conflict handling and browser-open control.
"""

from __future__ import annotations

import re
import socket
import threading
import urllib.error
import urllib.request

import pytest
from typer.testing import CliRunner

from infra.cli.main import app
from infra.cli.serve_cmd import (
    DEFAULT_PORT,
    _DashboardHandler,
    _DashboardHTTPServer,
    make_server,
    render_dashboard,
    serve_cmd,
)

runner = CliRunner()

APP = (
    "service frontend {\n"
    '    image: "nginx:1.25"\n'
    "    depends_on: [api]\n"
    "}\n"
    "\n"
    "service api {\n"
    '    image: "myapp:1.0"\n'
    "    port: 9000\n"
    "    replicas: 2\n"
    "    depends_on: [db]\n"
    "}\n"
    "\n"
    "database db {\n"
    '    type: "postgres"\n'
    "}\n"
    "\n"
    'environment "prod" {\n'
    "    service api {\n"
    "        replicas: 5\n"
    "    }\n"
    "}\n"
)

BROKEN = "service { this is not valid infra"


@pytest.fixture()
def infra_file(tmp_path):
    path = tmp_path / "app.infra"
    path.write_text(APP, encoding="utf-8")
    return path


@pytest.fixture()
def live_server(infra_file):
    """Real threaded server on an OS-assigned port (127.0.0.1)."""
    server = make_server(infra_file, 0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _get(url: str) -> tuple[int, str, str]:
    """Return (status, content_type, body_text); errors map to their code."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, resp.headers.get_content_type(), body
    except urllib.error.HTTPError as exc:
        return exc.code, "", ""


class TestOutputHtml:
    def test_serve_writes_static_report(self, infra_file, tmp_path):
        out = tmp_path / "report.html"
        result = runner.invoke(
            app, ["serve", str(infra_file), "--output-html", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert "[OK] HTML report written:" in result.output
        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert 'node-service" data-name="api"' in html
        assert 'data-from="api" data-to="db"' in html
        assert "finops-table" in html

    def test_ui_is_alias_of_serve(self, infra_file, tmp_path):
        out_serve = tmp_path / "a.html"
        out_ui = tmp_path / "b.html"
        r1 = runner.invoke(app, ["serve", str(infra_file), "-o", str(out_serve)])
        r2 = runner.invoke(app, ["ui", str(infra_file), "-o", str(out_ui)])
        assert r1.exit_code == 0 and r2.exit_code == 0
        assert out_serve.read_text(encoding="utf-8") == out_ui.read_text(
            encoding="utf-8"
        )

    def test_output_html_with_environment_overlay(self, infra_file, tmp_path):
        out = tmp_path / "env.html"
        result = runner.invoke(
            app, ["serve", str(infra_file), "-e", "prod", "-o", str(out)]
        )
        assert result.exit_code == 0, result.output
        html = out.read_text(encoding="utf-8")
        assert 'data-active-env="prod"' in html
        assert '<option value="prod" selected>' in html

    def test_missing_file_exits_1(self, tmp_path):
        result = runner.invoke(
            app, ["serve", str(tmp_path / "nope.infra"),
                  "-o", str(tmp_path / "x.html")]
        )
        assert result.exit_code == 1
        assert "Source file not found" in result.output

    def test_broken_file_exits_1_without_serving(self, tmp_path):
        bad = tmp_path / "bad.infra"
        bad.write_text(BROKEN, encoding="utf-8")
        result = runner.invoke(
            app, ["serve", str(bad), "-o", str(tmp_path / "x.html")]
        )
        assert result.exit_code == 1
        assert not (tmp_path / "x.html").exists()

    def test_unknown_environment_exits_1(self, infra_file, tmp_path):
        result = runner.invoke(
            app, ["serve", str(infra_file), "-e", "no-such-env",
                  "-o", str(tmp_path / "x.html")]
        )
        assert result.exit_code == 1


def _remember_open(opened: list[str]):
    def _fake(url, new=2):
        opened.append(url)
        return True

    return _fake


def _interrupting_serve_forever(self, poll_interval=0.5):
    raise KeyboardInterrupt


class TestRenderDashboard:
    def test_returns_full_document(self, infra_file):
        html = render_dashboard(infra_file, None)
        assert html.startswith("<!DOCTYPE html>")
        assert "Architecture DAG" in html

    def test_environment_applied(self, infra_file):
        html = render_dashboard(infra_file, "prod")
        assert 'data-active-env="prod"' in html


class TestLiveServer:
    def test_serves_dashboard_on_root(self, live_server):
        status, ctype, body = _get(f"http://127.0.0.1:{live_server.server_port}/")
        assert status == 200
        assert ctype == "text/html"
        assert 'node-service" data-name="api"' in body
        assert "finops-table" in body

    def test_index_html_alias(self, live_server):
        status, _c, body = _get(
            f"http://127.0.0.1:{live_server.server_port}/index.html"
        )
        assert status == 200
        assert "<!DOCTYPE html>" in body

    def test_unknown_path_returns_404(self, live_server):
        status, _c, _b = _get(
            f"http://127.0.0.1:{live_server.server_port}/favicon.ico"
        )
        assert status == 404

    def test_content_regenerated_after_file_change(self, live_server, infra_file):
        url = f"http://127.0.0.1:{live_server.server_port}/"
        _s, _c, first = _get(url)
        assert 'data-name="worker"' not in first
        infra_file.write_text(
            APP + '\nservice worker {\n    image: "jobs:1.0"\n}\n',
            encoding="utf-8",
        )
        _s, _c, second = _get(url)
        assert 'data-name="worker"' in second

    def test_broken_file_after_start_returns_500(self, live_server, infra_file):
        infra_file.write_text(BROKEN, encoding="utf-8")
        status, _c, _b = _get(f"http://127.0.0.1:{live_server.server_port}/")
        assert status == 500

    def test_server_binds_loopback_only(self, live_server):
        host, _port = live_server.server_address
        assert host == "127.0.0.1"

    def test_make_server_defaults_port_and_env(self, infra_file):
        server = make_server(infra_file, 0)
        try:
            assert server.server_port > 0
            assert isinstance(server, _DashboardHTTPServer)
            assert server.daemon_threads is True
        finally:
            server.server_close()

    def test_handler_overrides_access_log(self):
        # log_message must not write to stderr (routes to logging instead).
        assert "log_message" in _DashboardHandler.__dict__

    def test_default_port_constant(self):
        assert DEFAULT_PORT == 8080


class TestServeFlow:
    def test_port_conflict_exits_1(self, infra_file):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy_port = blocker.getsockname()[1]
        try:
            result = runner.invoke(
                app,
                ["serve", str(infra_file), "--port", str(busy_port),
                 "--no-browser"],
            )
        finally:
            blocker.close()
        assert result.exit_code == 1
        assert "Cannot bind" in result.output

    def test_broken_file_before_bind_exits_1(self, tmp_path):
        bad = tmp_path / "bad.infra"
        bad.write_text(BROKEN, encoding="utf-8")
        result = runner.invoke(
            app, ["serve", str(bad), "--port", "0", "--no-browser"]
        )
        assert result.exit_code == 1

    def test_ctrl_c_stops_server_cleanly(self, infra_file, monkeypatch):
        opened: list[str] = []
        monkeypatch.setattr(
            _DashboardHTTPServer, "serve_forever", _interrupting_serve_forever
        )
        monkeypatch.setattr(
            "infra.cli.serve_cmd.webbrowser.open", _remember_open(opened)
        )
        result = runner.invoke(
            app, ["serve", str(infra_file), "--port", "0"]
        )
        assert result.exit_code == 0, result.output
        assert "[OK] Dashboard ready: http://localhost:" in result.output
        assert "[OK] Server stopped." in result.output
        assert opened, "browser open expected when --no-browser is absent"

    def test_no_browser_flag_skips_open(self, infra_file, monkeypatch):
        opened: list[str] = []
        monkeypatch.setattr(
            _DashboardHTTPServer, "serve_forever", _interrupting_serve_forever
        )
        monkeypatch.setattr(
            "infra.cli.serve_cmd.webbrowser.open", _remember_open(opened)
        )
        result = runner.invoke(
            app, ["serve", str(infra_file), "--port", "0", "--no-browser"]
        )
        assert result.exit_code == 0
        assert "[SKIP] Browser auto-open disabled" in result.output
        assert opened == []

    def test_browser_open_failure_is_skip_not_crash(self, infra_file, monkeypatch):
        def broken_open(url, new=2):
            raise OSError("no $DISPLAY")

        monkeypatch.setattr(
            _DashboardHTTPServer, "serve_forever", _interrupting_serve_forever
        )
        monkeypatch.setattr("infra.cli.serve_cmd.webbrowser.open", broken_open)
        result = runner.invoke(
            app, ["serve", str(infra_file), "--port", "0"]
        )
        assert result.exit_code == 0
        assert "[SKIP] Could not open a browser automatically." in result.output

    def test_serve_help_lists_all_options(self):
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        # Rich may inject ANSI styling into --help on CI (forced color /
        # non-TTY runners); strip escape codes before content assertions.
        clean_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        for flag in ("--port", "--no-browser", "--env", "--output-html"):
            assert flag in clean_output

    def test_ui_help_shows_alias_text(self):
        result = runner.invoke(app, ["ui", "--help"])
        assert result.exit_code == 0
        assert "dashboard" in result.output.lower()

    def test_command_function_exists(self):
        assert callable(serve_cmd)
