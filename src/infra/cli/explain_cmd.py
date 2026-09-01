"""`infra explain` — human & AI-readable architecture analysis (v0.9.0).

Renders an insight report over an .infra file using only the existing
static analyzers (cost, security, reliability, validator) — no AI/ML
runtime. All natural-language fragments are deterministic templates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import typer

from infra.explain import collect_explain_data
from infra.explain.renderer import AUDIENCES, FORMATS, parse_sections, render_explain
from infra.parser import parse_file


def explain(
    file: Path = typer.Argument(..., help=".infra file to analyze"),
    output_format: str = typer.Option(
        "markdown", "--format", "-f", help="Report format: text | json | markdown"
    ),
    audience: str = typer.Option(
        "human",
        "--for",
        help="Optimize the report for: ai | human",
    ),
    sections: str = typer.Option(
        "all",
        "--sections",
        help="Comma-separated sections "
        "(overview,services,deps,cost,security,reliability,whatif) or 'all'",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write the report to a file instead of stdout"
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
    var: List[str] = typer.Option(
        [], "--var", help="Variable override: --var key=value"
    ),
) -> None:
    """Explain the architecture of an .infra file (insight report)."""
    from rich.console import Console

    console = Console()

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

    aud = audience.lower()
    if aud not in AUDIENCES:
        console.print(
            f"[red]Unknown audience '{audience}'.[/red] "
            f"Valid audiences: {', '.join(AUDIENCES)}"
        )
        raise typer.Exit(code=1)

    try:
        selected = parse_sections(sections)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    for item in var:
        if "=" not in item:
            console.print(
                f"[red]Invalid --var '{item}'.[/red] Expected key=value."
            )
            raise typer.Exit(code=1)

    try:
        source = file.read_text(encoding="utf-8")
        program = parse_file(file)
    except Exception as exc:
        detail = str(exc).splitlines()[0] if str(exc) else "parse error"
        console.print(f"[red]Cannot parse {file}:[/red] {detail}")
        raise typer.Exit(code=1) from exc

    from infra.cli.compile import _apply_environment

    program = _apply_environment(program, environment or "")

    data = collect_explain_data(program, source=source, project=file.stem)
    # Deterministic for an unchanged input file: the timestamp is derived
    # from the file mtime (not the wall clock), so two runs on the same
    # file produce byte-identical output.
    now = datetime.fromtimestamp(
        file.stat().st_mtime, timezone.utc
    ).isoformat(timespec="seconds")
    report = render_explain(
        data,
        output_format=fmt,
        audience=aud,
        sections=selected,
        now=now,
    )

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report if report.endswith("\n") else report + "\n",
                          encoding="utf-8")
        console.print(f"[green]Report written to[/green] {output}")
        return
    typer.echo(report)


__all__ = ["explain"]
