"""`infra cost` — static monthly cost estimation for an .infra file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer

from infra.analyzer.cost import CostEstimate, estimate_cost
from infra.parser import parse_file

#: Approximate USD->currency conversion factors (rough, for display only).
_CURRENCY_FACTORS = {"USD": 1.0, "EUR": 0.92, "PLN": 4.0}


def _render_table(
    console: Any, est: CostEstimate, factor: float, currency: str
) -> None:
    from rich.table import Table

    table = Table(title="Estimated monthly infrastructure cost")
    table.add_column("Resource", style="cyan")
    table.add_column("Kind")
    table.add_column("vCPU")
    table.add_column("RAM (GB)")
    table.add_column("Storage (GB)")
    table.add_column(f"Monthly ({currency})", justify="right", style="green")

    for item in est.items:
        table.add_row(
            item.name,
            item.kind,
            f"{item.vcpu:.2f}",
            f"{item.ram_gb:.2f}",
            f"{item.storage_gb:.2f}",
            f"{item.monthly_usd * factor:.2f}",
        )
    table.add_row(
        "TOTAL",
        "",
        "",
        "",
        "",
        f"{est.total_monthly_usd * factor:.2f}",
        style="bold",
    )
    console.print(table)


#: Valid values for the --format option.
_FORMATS = ("table", "json", "markdown", "html")


def _cost_all(
    currency: str,
    json_output: bool,
    output_format: str,
    output_file: Optional[Path],
    environment: Optional[str],
) -> None:
    """Estimate every .infra file in the workspace and render a summary."""
    from infra.cli import batch as _batch
    from infra.cli.compile import _apply_environment

    if output_file is not None or output_format.lower() not in ("table", "json"):
        typer.echo(
            "Error: --output and --format markdown/html are not supported "
            "with --all; use --json for machine-readable output.",
            err=True,
        )
        raise typer.Exit(code=1)

    currency_upper = currency.upper()
    factor = _CURRENCY_FACTORS.get(currency_upper, 1.0)

    root = Path.cwd()
    rows: list[_batch.BatchRow] = []
    total_usd = 0.0
    for f in _batch.discover_infra_files(root):
        rel = _batch.display_path(f, root)
        try:
            program = parse_file(f)
            program = _apply_environment(program, environment or "")
            est = estimate_cost(program)
        except Exception as exc:
            detail = str(exc).splitlines()[0] if str(exc) else "parse error"
            rows.append(_batch.BatchRow(rel, ok=False, errors=1, detail=detail))
            continue
        total_usd += est.total_monthly_usd
        rows.append(
            _batch.BatchRow(
                rel,
                ok=True,
                detail=f"{est.total_monthly_usd * factor:.2f} {currency_upper}/mo",
                extra={"monthly_usd": round(est.total_monthly_usd, 2)},
            )
        )
    _batch.emit_batch(
        "cost",
        rows,
        title="infra cost --all",
        verb="Estimated",
        json_output=json_output,
        extra={"total_monthly_usd": round(total_usd, 2), "currency": "USD"},
    )
    if _batch.any_failed(rows):
        raise typer.Exit(code=1)


def _format_report(est: CostEstimate, fmt: str, currency: str, factor: float) -> str:
    """Render the estimate in a text format (json | markdown | html)."""
    if fmt == "json":
        return json.dumps(est.to_dict(), indent=2)
    if fmt == "markdown":
        return est.to_markdown(currency=currency, factor=factor)
    return est.to_html(currency=currency, factor=factor)


def cost_cmd(
    file: Optional[Path] = typer.Argument(None, help=".infra file to estimate"),
    currency: str = typer.Option("USD", "--currency", help="USD | EUR | PLN"),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON"),
    output_format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: table | json | markdown | html",
    ),
    output_file: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the report to a file instead of stdout (markdown/html/json).",
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
    all_files: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Recursively estimate every .infra file under the current directory.",
    ),
) -> None:
    """Estimate the monthly cloud cost of an .infra file."""
    from rich.console import Console

    console = Console()

    if all_files:
        _cost_all(currency, json_output, output_format, output_file, environment)
        return

    if file is None:
        from infra.cli import batch as _batch

        raise _batch.usage_error("cost")
    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    fmt = output_format.lower()
    if fmt not in _FORMATS:
        console.print(
            f"[red]Unknown format '{output_format}'.[/red] "
            "Valid formats: table, json, markdown, html"
        )
        raise typer.Exit(code=1)

    from infra.cli.compile import _apply_environment

    program = _apply_environment(parse_file(file), environment or "")
    est = estimate_cost(program)

    # --json is kept for backward compatibility; it wins over --format table.
    if json_output and fmt == "table":
        fmt = "json"

    currency_upper = currency.upper()
    factor = _CURRENCY_FACTORS.get(currency_upper, 1.0)

    if fmt == "table":
        if output_file is not None:
            console.print(
                "[red]--output requires a text format:[/red] "
                "use --format json|markdown|html"
            )
            raise typer.Exit(code=1)
        _render_table(console, est, factor, currency_upper)
        return

    report = _format_report(est, fmt, currency_upper, factor)
    if output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(report + "\n", encoding="utf-8")
        console.print(f"[green]Report written to[/green] {output_file}")
        return
    typer.echo(report)
