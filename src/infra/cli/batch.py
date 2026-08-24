"""Batch workspace processing (``--all``) shared by CLI commands (v0.4.5).

Discovers ``*.infra`` files recursively under a root directory — skipping
hidden and vendor/build folders — and renders per-file results either as
a Rich summary table for humans or as an aggregate JSON document for
CI/CD pipelines.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import typer

#: Directories never entered during a recursive workspace scan.
_SKIP_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        "out",
        ".tox",
        ".nox",
    }
)


def discover_infra_files(root: Path) -> List[Path]:
    """Return every ``*.infra`` file under *root*, sorted deterministically.

    Hidden directories (``.git``, ``.idea`` …) and common vendor/build
    folders are skipped so a workspace scan never wanders into
    dependencies or generated output.
    """
    root = Path(root)
    found: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            if name.endswith(".infra"):
                found.append(Path(dirpath) / name)
    return sorted(set(found))


def usage_error(command: str) -> typer.Exit:
    """Build the exit raised when neither files nor ``--all`` were given."""
    typer.echo(
        "Error: nothing to do — pass .infra file(s) or use --all to scan "
        f"the workspace, e.g. `infra {command} --all`.",
        err=True,
    )
    return typer.Exit(code=2)


def display_path(path: Path, root: Path) -> str:
    """Render *path* relative to *root* when possible (shorter table rows)."""
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


@dataclass
class BatchRow:
    """The outcome of processing a single file in a batch run."""

    path: str
    ok: bool
    errors: int = 0
    warnings: int = 0
    detail: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def render_batch_table(rows: Sequence[BatchRow], console: Any, title: str) -> None:
    """Print a per-file results table (File | Status | Errors | Warnings | Detail)."""
    from rich.markup import escape
    from rich.table import Table

    table = Table(title=title)
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Errors", justify="right")
    table.add_column("Warnings", justify="right")
    table.add_column("Detail")
    for r in rows:
        status = "[green]OK[/green]" if r.ok else "[red]FAIL[/red]"
        detail = escape(r.detail) if r.detail else ""
        if not r.ok and detail:
            detail = f"[red]{detail}[/red]"
        table.add_row(r.path, status, str(r.errors), str(r.warnings), detail)
    console.print(table)


def batch_payload(
    command: str,
    rows: Sequence[BatchRow],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Aggregate JSON document for CI/CD consumers."""
    payload: Dict[str, Any] = {
        "command": command,
        "files": len(rows),
        "valid": sum(1 for r in rows if r.ok),
        "errors": sum(r.errors for r in rows),
        "warnings": sum(r.warnings for r in rows),
        "results": [
            {
                "file": r.path,
                "ok": r.ok,
                "errors": r.errors,
                "warnings": r.warnings,
                "detail": r.detail,
                **r.extra,
            }
            for r in rows
        ],
    }
    if extra:
        payload.update(extra)
    return payload


def emit_batch(
    command: str,
    rows: Sequence[BatchRow],
    *,
    title: str,
    verb: str,
    json_output: bool = False,
    extra: Optional[Dict[str, Any]] = None,
    summary: Optional[str] = None,
) -> None:
    """Render batch results: rich table + one-line summary, or JSON.

    ``verb`` produces the ``"<Verb> N files: V valid, E errors"`` summary
    unless *summary* overrides it. An empty scan prints a notice instead
    of an empty table.
    """
    total = len(rows)
    valid = sum(1 for r in rows if r.ok)
    errors = sum(r.errors for r in rows)
    if json_output:
        typer.echo(json.dumps(batch_payload(command, rows, extra), indent=2))
        return
    if total == 0:
        typer.echo("No .infra files found in the workspace scan.")
        return
    from rich.console import Console

    console = Console(highlight=False)
    render_batch_table(rows, console, title)
    typer.echo(summary or f"{verb} {total} files: {valid} valid, {errors} errors")


def any_failed(rows: Sequence[BatchRow]) -> bool:
    """True when at least one file failed in a batch run."""
    return any(not r.ok for r in rows)
