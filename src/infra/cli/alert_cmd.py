"""`infra alert` — Slack/Teams/Discord notifications for a .infra file.

Evaluates a file against alert conditions (monthly cost limit, SEC* security
violations, optionally live drift) and posts the result to webhooks — from
``--webhook`` URLs and/or an ``.infra-alert.yml`` config file. Delivery is a
plain HTTP POST with a timeout; URLs are masked in all output (they carry
secrets), and ``--dry-run`` renders payloads without sending anything.

Exit codes: ``0`` — delivered (or nothing to send); ``1`` — evaluation or
delivery failure; ``2`` — usage errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

import typer

from infra.alerts.webhooks import (
    ALL_EVENTS,
    DEFAULT_TIMEOUT,
    FORMATS,
    AlertConfigError,
    AlertContext,
    WebhookTarget,
    build_payload,
    evaluate_alerts,
    load_alert_config,
    mask_url,
    post_webhook,
)
from infra.analyzer.drift import DriftReport
from infra.parser import ast_nodes as n
from infra.parser import parse_file


def _probe_drift_safely(
    program: n.Program, target: str, namespace: str
) -> DriftReport:
    """Run the live drift probe, converting ANY failure into a report error.

    Mirrors the fail-safe contract of `infra serve --live-drift`: the probe
    is strictly read-only and alerting must never crash on kubectl/docker
    problems.
    """
    from infra.analyzer.drift import detect_live_drift_program

    try:
        return detect_live_drift_program(
            program, target=target, namespace=namespace
        )
    except Exception as exc:  # noqa: BLE001 - fail-safe by design
        return DriftReport(target=target, error=f"drift probe failed: {exc}")


def _parse_events_option(raw: Optional[str]) -> Optional[List[str]]:
    """Validate a comma-separated --events value (``None`` = all events)."""
    if raw is None:
        return None
    events = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [e for e in events if e not in ALL_EVENTS]
    if unknown:
        raise AlertConfigError(
            f"unknown event(s) {unknown}; valid: {list(ALL_EVENTS)}"
        )
    return events or None


def _deliver(
    console: Any,
    targets: List[WebhookTarget],
    ctx: AlertContext,
    *,
    timeout: float,
    dry_run: bool,
    always: bool,
) -> bool:
    """Deliver *ctx* to every subscribed target; return overall success."""
    ok = True
    for target in targets:
        events = [e for e in ctx.events if target.accepts(e)]
        if not events and not always:
            console.print(
                f"[SKIP] {mask_url(target.url)} — no subscribed alert "
                "conditions triggered."
            )
            continue
        sub_ctx = AlertContext(
            source=ctx.source,
            monthly_usd=ctx.monthly_usd,
            max_monthly_cost=ctx.max_monthly_cost,
            events=events,
        )
        try:
            payload = build_payload(target.format, sub_ctx)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            ok = False
            continue
        if dry_run:
            console.print(
                f"[DRY-RUN] {target.format} payload for "
                f"{mask_url(target.url)}:"
            )
            typer.echo(json.dumps(payload, indent=2))
            continue
        sent, detail = post_webhook(target.url, payload, timeout=timeout)
        if sent:
            console.print(
                f"[OK] {target.format} alert sent to "
                f"{mask_url(target.url)} ({detail})."
            )
        else:
            console.print(
                f"[FAIL] {target.format} alert to {mask_url(target.url)} "
                f"failed: {detail}"
            )
            ok = False
    return ok


def alert_cmd(
    file: Path = typer.Argument(..., help=".infra file to evaluate"),
    webhook: Optional[List[str]] = typer.Option(
        None, "--webhook", help="Webhook URL (repeatable)."
    ),
    output_format: str = typer.Option(
        "slack",
        "--format",
        "-f",
        help="Payload format for --webhook targets: slack | teams | discord",
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to an .infra-alert.yml file with webhook subscriptions.",
    ),
    max_monthly_cost: Optional[float] = typer.Option(
        None,
        "--max-monthly-cost",
        help="Trigger the cost_exceeded event above this USD/month amount.",
    ),
    events: Optional[str] = typer.Option(
        None,
        "--events",
        help="Comma-separated event filter: drift,cost_exceeded,"
        "security_violation (default: all).",
    ),
    live_drift: bool = typer.Option(
        False,
        "--live-drift",
        "--drift",
        help="Also probe the live state (read-only) for drift.",
    ),
    target: str = typer.Option(
        "k8s", "--target", "-t", help="Live drift probe target: k8s | compose"
    ),
    namespace: str = typer.Option(
        "default", "--namespace", "-n", help="Namespace for the k8s probe."
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT, "--timeout", help="HTTP timeout per webhook (seconds)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Render payloads without sending anything."
    ),
    always: bool = typer.Option(
        False,
        "--always",
        help="Send even when no alert condition triggered (all-clear).",
    ),
) -> None:
    """Send Slack/Teams/Discord alerts for cost, security and drift."""
    from rich.console import Console

    console = Console(stderr=True)

    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    fmt = output_format.lower()
    if fmt not in FORMATS:
        console.print(
            f"[red]Unknown format '{output_format}'.[/red] "
            f"Valid formats: {', '.join(FORMATS)}"
        )
        raise typer.Exit(code=1)

    try:
        wanted_events = _parse_events_option(events)
    except AlertConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    config_max: Optional[float] = None
    targets: List[WebhookTarget] = []
    if config is not None:
        if not config.exists():
            console.print(f"[red]Config file not found:[/red] {config}")
            raise typer.Exit(code=1)
        try:
            alert_config = load_alert_config(config)
        except AlertConfigError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        config_max = alert_config.max_monthly_cost
        targets.extend(alert_config.webhooks)

    for url in webhook or []:
        targets.append(
            WebhookTarget(url=url, format=fmt, events=wanted_events)
        )

    if not targets:
        console.print(
            "[red]No webhook targets.[/red] Pass --webhook <url> and/or "
            "--config <.infra-alert.yml>."
        )
        raise typer.Exit(code=2)

    effective_max = (
        max_monthly_cost if max_monthly_cost is not None else config_max
    )

    from infra.cli.compile import _apply_environment

    try:
        program = _apply_environment(parse_file(file), environment or "")
    except typer.Exit:
        raise
    except Exception as exc:  # parse errors et al.
        console.print(f"[red]alert failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    drift_report: Optional[DriftReport] = None
    if live_drift:
        drift_report = _probe_drift_safely(program, target, namespace)
        if drift_report.error:
            console.print(f"[SKIP] live drift probe: {drift_report.error}")

    ctx = evaluate_alerts(
        program,
        source=str(file),
        max_monthly_cost=effective_max,
        drift_report=drift_report,
        events=wanted_events,
    )

    if not ctx.triggered and not always:
        console.print(
            "[SKIP] no alert conditions triggered "
            "(use --always to notify anyway)."
        )
        return

    ok = _deliver(
        console, targets, ctx, timeout=timeout, dry_run=dry_run, always=always
    )
    if not ok:
        raise typer.Exit(code=1)
