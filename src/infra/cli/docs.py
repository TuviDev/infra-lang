"""`infra docs` command — generate documentation from .infra files."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from infra.parser import _parser
from infra.parser import ast_nodes as n


def docs(
    files: List[Path] = typer.Argument(..., help=".infra file(s)"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output markdown file"
    ),
) -> None:
    """Generate a Markdown inventory of the defined resources."""
    parser = _parser()
    lines = ["# Infra Inventory", ""]
    for f in files:
        program = parser.parse_file(f)
        lines.append(f"## `{f}`")
        for stmt in program.statements:
            loc = getattr(getattr(stmt, "location", None), "file", "")
            if loc == "<prelude>":
                continue  # built-in constants injected into every program
            lines.append(_D._describe(stmt))
    text = "\n".join(lines) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
        typer.echo(f"✅ Wrote docs to {output}")
    else:
        typer.echo(text)


class _D:
    @staticmethod
    def _describe(stmt: n.ASTNode) -> str:
        if isinstance(stmt, n.ServiceDef):
            return f"- **service** `{stmt.name}` (image: {stmt.image})"
        if isinstance(stmt, n.DatabaseDef):
            return f"- **database** `{stmt.name}` ({stmt.type})"
        if isinstance(stmt, n.CacheDef):
            return f"- **cache** `{stmt.name}` ({stmt.type})"
        if isinstance(stmt, n.QueueDef):
            return f"- **queue** `{stmt.name}` ({stmt.type})"
        if isinstance(stmt, n.StorageDef):
            return f"- **storage** `{stmt.name}` ({stmt.type})"
        if isinstance(stmt, n.PipelineDef):
            return f"- **pipeline** `{stmt.name}`"
        if isinstance(stmt, n.SecretDef):
            return f"- **secret** `{stmt.name}`"
        if isinstance(stmt, n.ConfigDef):
            return f"- **config** `{stmt.name}`"
        if isinstance(stmt, n.NetworkDef):
            return f"- **network** `{stmt.name}`"
        if isinstance(stmt, n.EnvironmentDef):
            return f"- **environment** `{stmt.name}`"
        if isinstance(stmt, n.ClusterDef):
            return f"- **cluster** `{stmt.name}`"
        return f"- **{type(stmt).__name__.lower()}** `{getattr(stmt, 'name', '?')}`"
