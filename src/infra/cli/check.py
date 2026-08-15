"""`infra check` command — quick syntax-only check."""

from __future__ import annotations

from pathlib import Path
from typing import List

import typer

from infra.parser import Parser


def check(
    files: List[Path] = typer.Argument(..., help=".infra file(s) to check")
) -> None:
    """Check syntax only (no semantic analysis)."""
    parser = Parser()
    ok = True
    for f in files:
        try:
            parser.parse_file(f)
        except Exception as e:
            typer.echo(f"{f}: {e}")
            ok = False
    if ok:
        typer.echo(f"✅ {len(files)} file(s) syntactically valid")
    else:
        raise typer.Exit(code=1)
