"""`infra check` command — quick syntax check (+ optional cost guardrail)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from infra.parser import _parser


def check(
    files: List[Path] = typer.Argument(..., help=".infra file(s) to check"),
    max_cost: Optional[float] = typer.Option(
        None,
        "--max-cost",
        help="FinOps guardrail: fail with a COST_EXCEEDED error when the "
        "estimated monthly cost exceeds this budget (in USD).",
    ),
) -> None:
    """Check syntax only (no semantic analysis)."""
    parser = _parser()
    ok = True
    for f in files:
        try:
            program = parser.parse_file(f)
        except Exception as e:
            typer.echo(f"{f}: {e}")
            ok = False
            continue
        if max_cost is not None:
            from infra.analyzer.cost import (
                COST_EXCEEDED_CODE,
                COST_EXCEEDED_HINT,
                budget_exceeded_message,
            )

            message = budget_exceeded_message(program, max_cost)
            if message is not None:
                typer.echo(
                    f"{f}: error[{COST_EXCEEDED_CODE}] {message} "
                    f"Hint: {COST_EXCEEDED_HINT}"
                )
                ok = False
    if ok:
        typer.echo(f"[OK] {len(files)} file(s) syntactically valid")
    else:
        raise typer.Exit(code=1)
