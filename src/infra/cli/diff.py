"""`infra diff` command — compare two .infra files."""

from __future__ import annotations

from pathlib import Path

import typer

from infra.parser import _parser


def diff_cmd(
    file1: Path = typer.Argument(..., help="First .infra file (before)"),
    file2: Path = typer.Argument(..., help="Second .infra file (after)"),
    format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text, json"
    ),
    only_changes: bool = typer.Option(
        False, "--only-changes", help="Show only changed items"
    ),
) -> None:
    """Compare two .infra files field by field."""
    from infra.diff.engine import InfraDiff

    parser = _parser()
    p1 = parser.parse(file1.read_text(), filename=file1.name)
    p2 = parser.parse(file2.read_text(), filename=file2.name)
    result = InfraDiff().diff(p1, p2)

    if format == "json":
        typer.echo(result.format_json())
    else:
        typer.echo(result.format(color=True, only_changes=only_changes))
