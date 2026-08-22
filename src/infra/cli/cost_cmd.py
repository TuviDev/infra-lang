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


def cost_cmd(
    file: Path = typer.Argument(..., help=".infra file to estimate"),
    currency: str = typer.Option("USD", "--currency", help="USD | EUR | PLN"),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON"),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
) -> None:
    """Estimate the monthly cloud cost of an .infra file."""
    from rich.console import Console

    console = Console()
    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    from infra.cli.compile import _apply_environment

    program = _apply_environment(parse_file(file), environment or "")
    est = estimate_cost(program)

    if json_output:
        typer.echo(json.dumps(est.to_dict(), indent=2))
        return

    currency_upper = currency.upper()
    factor = _CURRENCY_FACTORS.get(currency_upper, 1.0)
    _render_table(console, est, factor, currency_upper)
