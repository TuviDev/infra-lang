"""`infra serve` / `infra ui` — interactive local web dashboard (v0.5.2).

Serves a live HTML dashboard (architecture DAG, FinOps calculator, drift
panel, environment preview) for a single ``.infra`` file using **only the
Python standard library** — no Flask/FastAPI/Django. The page is
regenerated on every HTTP request, so edits to the file are reflected on
reload. With ``--output-html`` a static single-file report is written to
disk instead (fully offline), without starting the HTTP server.
"""

from __future__ import annotations

import logging
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Callable, ClassVar, Optional

import typer

from infra.analyzer.cost import estimate_cost
from infra.analyzer.ui_generator import generate_ui_html
from infra.errors.exceptions import InfraError
from infra.parser import parse_file

#: Default HTTP port for the dashboard server.
DEFAULT_PORT = 8080

#: Loopback-only bind — the dashboard shows infrastructure cost/security
#: metadata and must not be exposed on the LAN by default.
_BIND_HOST = "127.0.0.1"

_LOG = logging.getLogger("infra.serve")


def render_dashboard(file: Path, environment: Optional[str]) -> str:
    """Parse + estimate *file* and return the current dashboard HTML.

    Raises ``typer.Exit(1)`` for unknown environment overlays (mirrors the
    behavior of `infra cost`) and ``InfraError`` on parse problems, leaving
    the caller to render a readable message.
    """
    from dataclasses import replace as _dc_replace

    from infra.cli.compile import _apply_environment

    base = parse_file(file)
    program = _apply_environment(base, environment or "")
    if environment:
        # The overlay API strips `environments` after merging (by design, so
        # backends never re-apply it) — but the dashboard's environment
        # switcher needs the original list to stay selectable.
        program = _dc_replace(program, environments=base.environments)
    return generate_ui_html(
        program,
        estimate_cost(program),
        drift_report=None,
        env_name=environment or None,
    )


class _DashboardHTTPServer(ThreadingMixIn, HTTPServer):
    """Thread-per-request HTTP server whose workers die with the process."""

    daemon_threads = True
    # Reuse-address OFF on purpose: on Windows, a socket created with
    # SO_REUSEADDR may bind a port already held by another reuse-address
    # socket (port hijack), so a busy port would NOT fail with EADDRINUSE
    # and `infra serve` would silently "succeed" instead of exiting 1.
    # Trade-off: a few seconds of TIME_WAIT rebind delay on POSIX restarts.
    allow_reuse_address = False


class _DashboardHandler(SimpleHTTPRequestHandler):
    """Serves the single-page dashboard, regenerated on every request."""

    server_version = "InfraDashboard"
    #: HTML producer bound by :func:`make_server` (one server per file).
    render_fn: ClassVar[Callable[[], str]] = lambda: ""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        if self.path not in ("/", "/index.html"):
            self.send_error(404, "Unknown page")
            return
        try:
            # Read through the class (not self) so the bound provider is NOT
            # treated as an instance method receiving `self`.
            body = type(self).render_fn().encode("utf-8")
        except InfraError as exc:
            message = f"Dashboard render failed: {exc}".encode(
                "utf-8", errors="replace"
            )
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Route access logs to the logger instead of stderr; the CLI prints
        # its own status lines.
        _LOG.debug("%s - %s", self.address_string(), format % args)


def make_server(
    file: Path, port: int, environment: Optional[str] = None
) -> _DashboardHTTPServer:
    """Build (not yet start) a dashboard HTTP server bound to ``file``.

    ``port=0`` lets the OS pick a free port (used by tests; the chosen port
    is then available as ``server.server_port``). Raises ``OSError`` when the
    address is already in use.
    """
    handler_cls = type(
        "_BoundDashboardHandler",
        (_DashboardHandler,),
        {"render_fn": staticmethod(lambda: render_dashboard(file, environment))},
    )
    return _DashboardHTTPServer((_BIND_HOST, port), handler_cls)


def _open_browser(url: str) -> bool:
    """Best-effort browser open; ``False`` on headless/broken setups."""
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001 - headless OS, no $DISPLAY, etc.
        _LOG.debug("webbrowser.open failed", exc_info=True)
        return False


def serve_cmd(
    file: Path = typer.Argument(..., help=".infra file to visualize"),
    port: int = typer.Option(
        DEFAULT_PORT, "--port", "-p", help="HTTP port to bind (127.0.0.1)."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not open the dashboard in a browser."
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
    output_html: Optional[Path] = typer.Option(
        None,
        "--output-html",
        "-o",
        help="Write a standalone HTML report and exit (no HTTP server).",
    ),
) -> None:
    """Serve (or export with ``-o``) the visual dashboard for a .infra file."""
    from rich.console import Console

    console = Console()

    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    if output_html is not None:
        try:
            html_text = render_dashboard(file, environment)
        except InfraError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        output_html.write_text(html_text, encoding="utf-8")
        console.print(f"[OK] HTML report written: {output_html.as_posix()}")
        return

    # Render once up-front so syntax/validation problems surface before the
    # server socket is announced as ready.
    try:
        render_dashboard(file, environment)
    except InfraError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        server = make_server(file, port, environment)
    except OSError as exc:
        console.print(
            f"[red]Cannot bind {_BIND_HOST}:{port}[/red] — {exc}"
        )
        raise typer.Exit(code=1) from exc

    url = f"http://localhost:{server.server_port}"
    console.print(f"[OK] Dashboard ready: {url}  (Ctrl+C to stop)")
    if no_browser:
        console.print("[SKIP] Browser auto-open disabled (--no-browser).")
    elif not _open_browser(url):
        console.print("[SKIP] Could not open a browser automatically.")

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    console.print("[OK] Server stopped.")
