"""`infra schema` command — export the JSON Schema of the .infra DSL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer


def schema(
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the schema to this file (default: print to stdout).",
    ),
) -> None:
    """Export the complete JSON Schema (draft-07) of the .infra DSL.

    The schema describes every top-level block (``service``, ``database``,
    ``environment``, ``network_policy``, ``secret_store`` …) together with
    its documented properties — register it in your editor as
    ``infra-schema.json`` to get completion/validation for JSON-facing
    tooling that consumes .infra documents.
    """
    from infra.schema import build_schema

    text = json.dumps(build_schema(), indent=2, ensure_ascii=True)
    if output is not None:
        output.write_text(text + "\n", encoding="utf-8")
        typer.echo(f"[OK] JSON Schema written to {output}")
    else:
        typer.echo(text)
