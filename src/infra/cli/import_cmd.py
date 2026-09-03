"""`infra import` — reverse-compile Kubernetes YAML back to Infra source."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer


def import_cmd(
    source: Path = typer.Argument(
        ...,
        help="Kubernetes YAML file or directory of manifests",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write generated .infra to this file (default: stdout)",
    ),
) -> None:
    """Import existing Kubernetes YAML and print (or save) Infra source."""
    from infra.importer import import_kubernetes

    try:
        text = import_kubernetes(source)
    except Exception as exc:  # InfraImportError and friends
        typer.echo(f"import error: {exc}", err=True)
        raise typer.Exit(code=1)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        typer.echo(f"[OK] Imported to {output}")
    else:
        typer.echo(text, nl=False)
