"""`infra graph` command — visualize the infrastructure dependency graph."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import typer

from infra.parser import _parser
from infra.parser import ast_nodes as n

_SHAPES = {
    "service": ("box", "service"),
    "database": ("cylinder", "database"),
    "cache": ("cylinder", "cache"),
    "queue": ("cylinder", "queue"),
}


def _collect(files: List[Path]) -> Tuple[Dict[str, Dict], List[Tuple[str, str]]]:
    """Return {name: {kind, sub, ingress_host}} nodes and depends edges."""
    parser = _parser()
    nodes: Dict[str, Dict] = {}
    edges: Set[Tuple[str, str]] = set()

    def _add(name: str, kind: str, sub: str, ingress_host: Optional[str] = None):
        if name not in nodes:
            nodes[name] = {"kind": kind, "sub": sub, "ingress_host": ingress_host}

    for f in files:
        program = parser.parse_file(f)
        for stmt in program.statements:
            if isinstance(stmt, n.ServiceDef):
                host = None
                if stmt.ingress is not None:
                    host = stmt.ingress.host or stmt.ingress.domain or None
                _add(stmt.name, "service", "service", host)
                for dep in stmt.depends:
                    edges.add((stmt.name, dep))
            elif isinstance(stmt, n.DatabaseDef):
                _add(stmt.name, "database", stmt.type or "database")
            elif isinstance(stmt, n.CacheDef):
                _add(stmt.name, "cache", stmt.type or "cache")
            elif isinstance(stmt, n.QueueDef):
                _add(stmt.name, "queue", stmt.type or "queue")
    return nodes, sorted(edges)


def _render_ascii(nodes: Dict[str, Dict], edges: List[Tuple[str, str]]) -> str:
    lines: List[str] = []

    def _label(name: str) -> str:
        meta = nodes.get(name)
        if not meta:
            return f"[{name}]"
        return f"[{meta['kind']}: {name}]"

    for src, dst in edges:
        lines.append(f"{_label(src)} ──► {_label(dst)}")
    for name, meta in sorted(nodes.items()):
        if meta["ingress_host"]:
            lines.append(
                f"{_label(name)} ◄── INGRESS ({meta['ingress_host']})"
            )
    return "\n".join(lines) if lines else "(no dependencies)"


def _render_dot(nodes: Dict[str, Dict], edges: List[Tuple[str, str]]) -> str:
    lines = ["digraph infra {", "    rankdir=LR", ""]
    for name, meta in sorted(nodes.items()):
        shape, _ = _SHAPES.get(meta["kind"], ("box", "node"))
        lines.append(
            f'    "{name}" [label="{name}\\n{meta["sub"]}" shape={shape}]'
        )
    lines.append("")
    for src, dst in edges:
        lines.append(f'    "{src}" -> "{dst}"')
    lines.append("}")
    return "\n".join(lines)


def _render_mermaid(nodes: Dict[str, Dict], edges: List[Tuple[str, str]]) -> str:
    lines = ["graph LR"]

    def _node(name: str, meta: Dict) -> str:
        kind = meta["kind"]
        label = f"{name} - {meta['sub']}"
        if kind == "database" or kind == "cache" or kind == "queue":
            return f'{name}("{label}")'
        return f"{name}[{label}]"

    for name, meta in sorted(nodes.items()):
        lines.append(f"    {_node(name, meta)}")
    for src, dst in edges:
        lines.append(f"    {src} --> {dst}")
    return "\n".join(lines)


def graph(
    files: List[Path] = typer.Argument(..., help=".infra file(s)"),
    format: str = typer.Option(
        "ascii", "--format", help="Output format: ascii, dot, mermaid"
    ),
    output: Optional[Path] = typer.Option(None, "--output", help="Write to file"),
) -> None:
    """Print the infrastructure dependency graph."""
    nodes, edges = _collect(files)
    if not nodes:
        body = "(no infrastructure)"
    elif format == "dot":
        body = _render_dot(nodes, edges)
    elif format == "mermaid":
        body = _render_mermaid(nodes, edges)
    else:  # ascii
        body = _render_ascii(nodes, edges)

    if output is not None:
        output.write_text(body + "\n")
        typer.echo(f"✅ Graph written to {output}")
    else:
        typer.echo(body)
