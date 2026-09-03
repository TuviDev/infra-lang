"""`infra fmt` command."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import List, Optional

import typer

from infra.cli import batch as _batch


def fmt(
    files: Optional[List[Path]] = typer.Argument(
        None, help=".infra file(s) to format"
    ),
    check: bool = typer.Option(
        False, "--check", help="Only check formatting (exit 1 if changed)"
    ),
    diff: bool = typer.Option(False, "--diff", help="Show a diff instead of writing"),
    indent: int = typer.Option(4, "--indent", help="Indent size"),
    all_files: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Recursively format every .infra file under the current directory.",
    ),
) -> None:
    """Format .infra files through the AST pretty-printer."""
    from infra.cli.printer import format_file

    if all_files:
        _fmt_all(check, diff, indent)
        return

    if not files:
        raise _batch.usage_error("fmt")

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
            typer.echo(_udiff(f, formatted))
        else:
            Path(f).write_text(formatted, encoding="utf-8")
    if check:
        typer.echo(f"{changed} file(s) need formatting; {unchanged} already formatted")
        if changed:
            raise typer.Exit(code=1)
    else:
        typer.echo(f"Formatted {changed} files; {unchanged} already formatted")


def _udiff(f: Path, formatted: str) -> str:
    original = Path(f).read_text(encoding="utf-8")
    udiff = difflib.unified_diff(
        original.splitlines(),
        formatted.splitlines(),
        fromfile=str(f),
        tofile=str(f),
        lineterm="",
    )
    return "\n".join(udiff)


def _fmt_all(check: bool, diff: bool, indent: int) -> None:
    """Batch-format every .infra file discovered in the workspace."""
    from infra.cli.printer import format_file

    root = Path.cwd()
    rows: list[_batch.BatchRow] = []
    changed = 0
    for f in _batch.discover_infra_files(root):
        rel = _batch.display_path(f, root)
        try:
            formatted, is_changed = format_file(f, indent)
        except Exception as exc:
            detail = str(exc).splitlines()[0] if str(exc) else "parse error"
            rows.append(_batch.BatchRow(rel, ok=False, errors=1, detail=detail))
            continue
        if not is_changed:
            rows.append(_batch.BatchRow(rel, ok=True, detail="already formatted"))
            continue
        changed += 1
        rows.append(
            _batch.BatchRow(
                rel,
                ok=True,
                warnings=1,
                detail="would reformat" if check or diff else "reformatted",
            )
        )
        if check:
            continue
        if diff:
            typer.echo(_udiff(f, formatted))
        else:
            Path(f).write_text(formatted, encoding="utf-8")
    failed = sum(r.errors for r in rows)
    if check:
        summary = (
            f"Checked {len(rows)} files: {changed} need formatting, "
            f"{failed} failed"
        )
    else:
        summary = f"Formatted {len(rows)} files: {changed} changed, {failed} failed"
    _batch.emit_batch(
        "fmt", rows, title="infra fmt --all", verb="Formatted", summary=summary
    )
    if _batch.any_failed(rows) or (check and changed):
        raise typer.Exit(code=1)
