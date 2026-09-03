"""`infra check` command — quick syntax check (+ optional cost guardrail)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from infra.cli import batch as _batch


def check(
    files: Optional[List[Path]] = typer.Argument(
        None, help=".infra file(s) to check"
    ),
    max_cost: Optional[float] = typer.Option(
        None,
        "--max-cost",
        help="FinOps guardrail: fail with a COST_EXCEEDED error when the "
        "estimated monthly cost exceeds this budget (in USD).",
    ),
    all_files: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Recursively check every .infra file under the current directory.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="With --all: emit an aggregate JSON document."
    ),
) -> None:
    """Check syntax only (no semantic analysis)."""
    from infra.parser import _parser

    parser = _parser()

    if all_files:
        root = Path.cwd()
        rows = []
        for f in _batch.discover_infra_files(root):
            rel = _batch.display_path(f, root)
            try:
                program = parser.parse_file(f)
            except Exception as e:
                detail = str(e).splitlines()[0] if str(e) else "parse error"
                rows.append(_batch.BatchRow(rel, ok=False, errors=1, detail=detail))
                continue
            if max_cost is not None:
                from infra.analyzer.cost import (
                    COST_EXCEEDED_CODE,
                    budget_exceeded_message,
                )

                message = budget_exceeded_message(program, max_cost)
                if message is not None:
                    rows.append(
                        _batch.BatchRow(
                            rel,
                            ok=False,
                            errors=1,
                            detail=f"{COST_EXCEEDED_CODE}: {message}",
                        )
                    )
                    continue
            rows.append(_batch.BatchRow(rel, ok=True))
        _batch.emit_batch(
            "check",
            rows,
            title="infra check --all",
            verb="Checked",
            json_output=json_output,
        )
        if _batch.any_failed(rows):
            raise typer.Exit(code=1)
        return

    if not files:
        raise _batch.usage_error("check")

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
