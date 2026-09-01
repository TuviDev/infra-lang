"""`infra graph` command — visualize the infrastructure dependency graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import typer

from infra.parser import _parser
from infra.parser import ast_nodes as n

_SHAPES = {
    "service": ("box", "service"),
    "database": ("cylinder", "database"),
    "cache": ("cylinder", "cache"),
    "queue": ("cylinder", "queue"),
}


def _collect(
    files: List[Path], environment: str = ""
) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str]]]:
    """Return {name: {kind, sub, ingress_host}} nodes and depends edges."""
    from infra.cli.compile import _apply_environment

    parser = _parser()
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Set[Tuple[str, str]] = set()

    def _add(
        name: str, kind: str, sub: str, ingress_host: Optional[str] = None
    ) -> None:
        if name not in nodes:
            nodes[name] = {"kind": kind, "sub": sub, "ingress_host": ingress_host}

    for f in files:
        program = _apply_environment(parser.parse_file(f), environment)
        for stmt in program.statements:
            if isinstance(stmt, n.ServiceDef):
                host = None
                if stmt.ingress is not None:
                    host = stmt.ingress.host or stmt.ingress.domain or None
                _add(stmt.name, "service", "service", host)
                for dep in stmt.dependencies:
                    edges.add((stmt.name, dep))
            elif isinstance(stmt, n.DatabaseDef):
                _add(stmt.name, "database", stmt.type or "database")
            elif isinstance(stmt, n.CacheDef):
                _add(stmt.name, "cache", stmt.type or "cache")
            elif isinstance(stmt, n.QueueDef):
                _add(stmt.name, "queue", stmt.type or "queue")
    return nodes, sorted(edges)


def _render_ascii(
    nodes: Dict[str, Dict[str, Any]], edges: List[Tuple[str, str]]
) -> str:
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


def _render_dot(nodes: Dict[str, Dict[str, Any]], edges: List[Tuple[str, str]]) -> str:
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


def _render_mermaid(
    nodes: Dict[str, Dict[str, Any]], edges: List[Tuple[str, str]]
) -> str:
    lines = ["graph LR"]

    def _node(name: str, meta: Dict[str, Any]) -> str:
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
        "ascii", "--format", help="Output format: ascii, dot, mermaid, svg, png"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write to file"
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
) -> None:
    """Print the dependency graph (or export it to SVG/PNG).

    The ``svg`` format renders the architecture DAG exactly like the
    dashboard (same collector and layout) as a self-contained ``.svg``
    document; the ``png`` format draws the same DAG natively with Pillow
    (dark theme, rounded nodes, arrowed edges). Both require exactly one
    input file, and ``png`` always needs ``-o/--output`` (binary output
    cannot echo to stdout). ``-o out.svg``/``-o out.png`` without an
    explicit ``--format`` implies the matching image format.
    """
    from infra.cli.compile import _apply_environment

    env_name = environment or ""
    fmt = format
    suffix = output.suffix.lower() if output is not None else ""
    if fmt == "ascii" and suffix in (".svg", ".png"):
        # ``-o out.svg`` says more than the (default) ascii format.
        fmt = suffix[1:]

    if fmt == "png":
        from infra.analyzer.graph_png import render_dag_png_bytes

        if len(files) != 1:
            typer.echo(
                "[FAIL] PNG export requires exactly one .infra file "
                f"(got {len(files)}).",
                err=True,
            )
            raise typer.Exit(code=1)
        if output is None:
            typer.echo(
                "[FAIL] PNG export requires --output/-o "
                "(binary data cannot be printed).",
                err=True,
            )
            raise typer.Exit(code=1)
        program = _apply_environment(
            _parser().parse_file(files[0]), env_name
        )
        output.write_bytes(render_dag_png_bytes(program))
        typer.echo(f"[OK] Graph written to {output}")
        return

    if fmt == "svg":
        from infra.analyzer.ui_generator import generate_dag_svg

        if len(files) != 1:
            typer.echo(
                "[FAIL] SVG export requires exactly one .infra file "
                f"(got {len(files)}).",
                err=True,
            )
            raise typer.Exit(code=1)
        program = _apply_environment(
            _parser().parse_file(files[0]), env_name
        )
        body = generate_dag_svg(program)
    else:
        nodes, edges = _collect(files, env_name)
        if not nodes:
            body = "(no infrastructure)"
        elif fmt == "dot":
            body = _render_dot(nodes, edges)
        elif fmt == "mermaid":
            body = _render_mermaid(nodes, edges)
        else:  # ascii
            body = _render_ascii(nodes, edges)

    if output is not None:
        output.write_text(body + "\n", encoding="utf-8")
        typer.echo(f"[OK] Graph written to {output}")
    else:
        typer.echo(body)
