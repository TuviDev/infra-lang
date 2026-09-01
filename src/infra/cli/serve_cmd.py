"""`infra serve` / `infra ui` — interactive local web dashboard (v0.5.2).

Serves a live HTML dashboard (architecture DAG, FinOps calculator, drift
panel, environment preview) for a single ``.infra`` file using **only the
Python standard library** — no Flask/FastAPI/Django. The page is
regenerated on every HTTP request, so edits to the file are reflected on
reload. With ``--output-html`` a static single-file report is written to
disk instead (fully offline), without starting the HTTP server. With
``--live-drift`` the drift panel shows a read-only live probe
(``kubectl``/``docker compose``) instead of an empty placeholder (v0.5.6).
With ``--publish <dir>`` a complete **static site** (index.html, one page
per environment, JSON summary and a timestamped history snapshot) is
generated for GitHub Pages / S3-style hosting — no backend, 0-cost
(v0.7.0).
"""

from __future__ import annotations

import json
import logging
import re
import webbrowser
from datetime import datetime, timezone
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

import typer

from infra.analyzer.cost import estimate_cost
from infra.analyzer.drift import DriftReport
from infra.analyzer.ui_generator import generate_compare_html, generate_ui_html
from infra.errors.exceptions import InfraError
from infra.parser import ast_nodes as n
from infra.parser import parse_file
from infra.version import __version__

#: Default HTTP port for the dashboard server.
DEFAULT_PORT = 8080

#: Loopback-only bind — the dashboard shows infrastructure cost/security
#: metadata and must not be exposed on the LAN by default.
_BIND_HOST = "127.0.0.1"

_LOG = logging.getLogger("infra.serve")


def _probe_drift_safely(
    program: n.Program, target: str, namespace: str
) -> DriftReport:
    """Run the live drift probe, converting ANY failure into a report error.

    The dashboard HTTP handler must never 500 just because a probe blew up:
    the drift engine already reports tool/connection problems via
    :attr:`DriftReport.error`, and this wrapper additionally catches engine-
    level exceptions (fail-safe UI, read-only contract preserved).
    """
    from infra.analyzer.drift import detect_live_drift_program

    try:
        return detect_live_drift_program(
            program, target=target, namespace=namespace
        )
    except Exception as exc:  # noqa: BLE001 - fail-safe by design
        _LOG.warning("live drift probe failed: %s", exc)
        return DriftReport(target=target, error=f"drift probe failed: {exc}")


def render_dashboard(
    file: Path,
    environment: Optional[str],
    live_drift: bool = False,
    target: str = "k8s",
    namespace: str = "default",
) -> str:
    """Parse + estimate *file* and return the current dashboard HTML.

    Raises ``typer.Exit(1)`` for unknown environment overlays (mirrors the
    behavior of `infra cost`) and ``InfraError`` on parse problems, leaving
    the caller to render a readable message. When *live_drift* is on, the
    drift panel is fed with a read-only live probe (never raises — probe
    failures surface as an ``error`` report rendered as a readable badge).
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
    drift_report: Optional[DriftReport] = None
    if live_drift:
        drift_report = _probe_drift_safely(program, target, namespace)
    return generate_ui_html(
        program,
        estimate_cost(program),
        drift_report=drift_report,
        env_name=environment or None,
    )


def render_compare(file: Path, env_a: str, env_b: str) -> str:
    """Parse *file* once and return the side-by-side comparison HTML.

    Raises ``InfraError`` on parse problems and
    ``EnvironmentNotFoundError`` (itself an ``InfraError``) for unknown
    overlay names — the special name ``base`` selects the unoverlaid file.
    """
    return generate_compare_html(parse_file(file), env_a, env_b)


#: Only these characters are kept when turning an environment name into a
#: static filename (3-OS safe, no path traversal).
_SAFE_PAGE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _page_name(env: str) -> str:
    """Filesystem-safe page name for an environment (never a path)."""
    safe = _SAFE_PAGE_NAME.sub("-", env)
    # Collapse dot/dash runs so nothing resembling a `..` segment survives
    # and names stay tidy ("weird/../name" -> "weird-name").
    safe = re.sub(r"\.{2,}", "-", safe)
    safe = re.sub(r"-{2,}", "-", safe).strip("-.")
    return safe or "env"


def _read_history_index(path: Path) -> List[Dict[str, Any]]:
    """Read an existing history index, tolerating missing/corrupt files."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def publish_site(
    file: Path,
    directory: Path,
    environment: Optional[str],
    live_drift: bool = False,
    target: str = "k8s",
    namespace: str = "default",
) -> Dict[str, Any]:
    """Generate a static, offline team dashboard site into *directory*.

    Layout::

        <dir>/index.html                 dashboard (base or -e overlay)
        <dir>/envs/<env>.html            one page per declared environment
        <dir>/data/summary.json          machine-readable snapshot
        <dir>/data/history/<ts>.json     timestamped run snapshot
        <dir>/data/history/index.json    append-only list of snapshots

    Re-running `infra ui --publish <dir>` (e.g. in CI on every merge) appends
    history snapshots — commit *directory* to a Pages/S3 branch for a 0-cost
    team cost & drift history.

    Raises ``InfraError`` on parse problems and ``typer.Exit(1)`` for
    unknown environment overlays (same contract as :func:`render_dashboard`).
    Returns a dict describing the written files (used by tests and the CLI
    status lines).
    """
    from dataclasses import replace as _dc_replace

    from infra.cli.compile import _apply_environment

    base = parse_file(file)
    program = _apply_environment(base, environment or "")
    if environment:
        program = _dc_replace(program, environments=base.environments)

    drift_report: Optional[DriftReport] = None
    if live_drift:
        drift_report = _probe_drift_safely(program, target, namespace)

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S-%fZ")
    generated_at = now.isoformat()

    directory.mkdir(parents=True, exist_ok=True)
    data_dir = directory / "data"
    history_dir = data_dir / "history"
    envs_dir = directory / "envs"
    history_dir.mkdir(parents=True, exist_ok=True)

    index_html = generate_ui_html(
        program,
        estimate_cost(program),
        drift_report=drift_report,
        env_name=environment or None,
    )
    (directory / "index.html").write_text(index_html, encoding="utf-8")

    env_pages: Dict[str, str] = {}
    if base.environments:
        envs_dir.mkdir(parents=True, exist_ok=True)
    for env_def in base.environments:
        overlaid = _dc_replace(
            _apply_environment(base, env_def.name),
            environments=base.environments,
        )
        page = f"envs/{_page_name(env_def.name)}.html"
        (directory / page).write_text(
            generate_ui_html(
                overlaid,
                estimate_cost(overlaid),
                env_name=env_def.name,
            ),
            encoding="utf-8",
        )
        env_pages[env_def.name] = page

    cost = estimate_cost(program)
    snapshot: Dict[str, Any] = {
        "tool": "infra-lang",
        "version": __version__,
        "source": str(file),
        "environment": environment or None,
        "generated_at": generated_at,
        "monthly_usd": cost.total_monthly_usd,
        "currency": "USD",
        "resources": [
            {
                "name": item.name,
                "kind": item.kind,
                "monthly_usd": item.monthly_usd,
            }
            for item in cost.items
        ],
        "environments": [e.name for e in base.environments],
        "drift": drift_report.to_dict() if drift_report is not None else None,
    }
    summary = dict(snapshot)
    summary["pages"] = {"index": "index.html", "environments": env_pages}
    (data_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    snapshot_name = f"{stamp}.json"
    (history_dir / snapshot_name).write_text(
        json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
    )
    index_path = history_dir / "index.json"
    history = _read_history_index(index_path)
    history.append(
        {
            "file": snapshot_name,
            "generated_at": generated_at,
            "environment": environment or None,
            "monthly_usd": cost.total_monthly_usd,
            "resources": len(cost.items),
            "drift": (
                drift_report.has_drift if drift_report is not None else None
            ),
        }
    )
    index_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")

    return {
        "directory": directory,
        "index": directory / "index.html",
        "summary": data_dir / "summary.json",
        "env_pages": env_pages,
        "snapshot": history_dir / snapshot_name,
        "history_count": len(history),
    }


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
    file: Path,
    port: int,
    environment: Optional[str] = None,
    static_html: Optional[str] = None,
    live_drift: bool = False,
    target: str = "k8s",
    namespace: str = "default",
) -> _DashboardHTTPServer:
    """Build (not yet start) a dashboard HTTP server bound to ``file``.

    ``port=0`` lets the OS pick a free port (used by tests; the chosen port
    is then available as ``server.server_port``). Raises ``OSError`` when the
    address is already in use. With *static_html* the server answers with the
    given page instead of re-rendering on each request (compare reports are
    snapshots — re-parsing on every reload would be wasteful). With
    *live_drift* each request also runs the read-only drift probe for
    *target* (``k8s``/``compose``) in *namespace*.
    """
    render_fn: Callable[[], str]
    if static_html is not None:

        def render_fn() -> str:  # snapshot page (compare report)
            return static_html

    else:
        render_fn = partial(
            render_dashboard,
            file,
            environment,
            live_drift,
            target,
            namespace,
        )
    handler_cls = type(
        "_BoundDashboardHandler",
        (_DashboardHandler,),
        {"render_fn": staticmethod(render_fn)},
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
    publish: Optional[Path] = typer.Option(
        None,
        "--publish",
        help="Generate a static dashboard SITE into this directory "
        "(index.html + env pages + data/summary.json + history snapshot) "
        "for Pages/S3 hosting, and exit (no HTTP server).",
    ),
    compare: Optional[Tuple[str, str]] = typer.Option(
        None,
        "--compare",
        help="Compare two environment overlays side by side "
        "(e.g. --compare base prod); 'base' = the unoverlaid file.",
    ),
    live_drift: bool = typer.Option(
        False,
        "--live-drift",
        "--drift",
        help="Probe the live state (kubectl / docker compose, strictly "
        "read-only) and render it in the Drift panel.",
    ),
    target: str = typer.Option(
        "k8s",
        "--target",
        "-t",
        help="Live drift probe target: k8s or compose (needs --live-drift).",
    ),
    namespace: str = typer.Option(
        "default",
        "--namespace",
        "-n",
        help="Kubernetes namespace for the k8s live drift probe.",
    ),
) -> None:
    """Serve (or export with ``-o``) the visual dashboard for a .infra file.

    With ``--compare <env_a> <env_b>`` the served/exported page is a static
    side-by-side comparison of the two overlays instead of the dashboard.
    With ``--live-drift`` the Drift panel shows a read-only live probe —
    missing tools and unreachable clusters render as readable badges, never
    crash the server.
    """
    from rich.console import Console

    console = Console()

    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    if compare is not None and environment:
        console.print(
            "[red]--compare cannot be combined with -e/--environment[/red]"
        )
        raise typer.Exit(code=1)

    if compare is not None and live_drift:
        console.print(
            "[red]--live-drift renders the dashboard drift panel; the "
            "compare report has none — use one or the other.[/red]"
        )
        raise typer.Exit(code=1)

    if publish is not None and compare is not None:
        console.print(
            "[red]--publish exports the dashboard site; the compare report "
            "is a single page — use -o/--output-html for it instead.[/red]"
        )
        raise typer.Exit(code=1)

    if publish is not None and output_html is not None:
        console.print(
            "[red]--publish already writes a static site — "
            "-o/--output-html is not needed (use one or the other).[/red]"
        )
        raise typer.Exit(code=1)

    if publish is not None:
        try:
            written = publish_site(
                file, publish, environment, live_drift, target, namespace
            )
        except InfraError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        console.print(
            f"[OK] Static dashboard published: {publish.as_posix()}"
        )
        console.print(f"[OK]   index: {written['index'].as_posix()}")
        if written["env_pages"]:
            names = ", ".join(sorted(written["env_pages"]))
            console.print(
                f"[OK]   environments ({len(written['env_pages'])}): {names}"
            )
        console.print(f"[OK]   summary: {written['summary'].as_posix()}")
        console.print(
            f"[OK]   history snapshot #{written['history_count']}: "
            f"{written['snapshot'].as_posix()}"
        )
        if live_drift:
            console.print(
                f"[OK]   live drift snapshot included "
                f"(target: {target}, namespace: {namespace}, read-only)."
            )
        return

    # Render once up-front so syntax/validation/unknown-overlay problems
    # surface before the server socket is announced as ready.
    try:
        if compare is not None:
            html_text = render_compare(file, compare[0], compare[1])
        else:
            html_text = render_dashboard(
                file, environment, live_drift, target, namespace
            )
    except InfraError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if output_html is not None:
        output_html.write_text(html_text, encoding="utf-8")
        label = "Compare report" if compare is not None else "HTML report"
        console.print(f"[OK] {label} written: {output_html.as_posix()}")
        return

    try:
        if compare is not None:
            server = make_server(file, port, static_html=html_text)
        else:
            server = make_server(
                file,
                port,
                environment,
                live_drift=live_drift,
                target=target,
                namespace=namespace,
            )
    except OSError as exc:
        console.print(
            f"[red]Cannot bind {_BIND_HOST}:{port}[/red] — {exc}"
        )
        raise typer.Exit(code=1) from exc

    url = f"http://localhost:{server.server_port}"
    label = "Compare report" if compare is not None else "Dashboard"
    console.print(f"[OK] {label} ready: {url}  (Ctrl+C to stop)")
    if live_drift:
        console.print(
            f"[OK] Live drift probe enabled (target: {target}, "
            f"namespace: {namespace}, read-only)."
        )
    if no_browser:
        console.print("[SKIP] Browser auto-open disabled (--no-browser).")
    elif not _open_browser(url):
        console.print("[SKIP] Could not open a browser automatically.")

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        # Ctrl+C is the intended shutdown path — fall through to server_close.
        pass
    finally:
        server.server_close()
    console.print("[OK] Server stopped.")
