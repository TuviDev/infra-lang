"""`infra fmt` command."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import List

import typer

from infra.cli.printer import format_file


def fmt(
    files: List[Path] = typer.Argument(..., help=".infra file(s) to format"),
    check: bool = typer.Option(
        False, "--check", help="Only check formatting (exit 1 if changed)"
    ),
    diff: bool = typer.Option(False, "--diff", help="Show a diff instead of writing"),
    indent: int = typer.Option(4, "--indent", help="Indent size"),
) -> None:
    """Format .infra files through the AST pretty-printer."""
    changed = 0
    unchanged = 0
    for f in files:
        formatted, is_changed = format_file(f, indent)
        if not is_changed:
            unchanged += 1
            continue
        changed += 1
        if check:
            typer.echo(f"{f}: would reformat")
            continue
        if diff:
            original = Path(f).read_text()
            udiff = difflib.unified_diff(
                original.splitlines(),
                formatted.splitlines(),
                fromfile=str(f),
                tofile=str(f),
                lineterm="",
            )
            typer.echo("\n".join(udiff))
        else:
            Path(f).write_text(formatted)
    if check:
        typer.echo(f"{changed} file(s) need formatting; {unchanged} already formatted")
        if changed:
            raise typer.Exit(code=1)
    else:
        typer.echo(f"Formatted {changed} files; {unchanged} already formatted")
