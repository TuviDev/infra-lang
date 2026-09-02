"""`infra workspace` — multi-project workspaces (init / list / check / compile).

The command group works on the ``infra-workspace.yaml`` manifest in the
current directory (see :mod:`infra.workspace.manager` for the schema). All
status lines use plain ASCII markers (``[OK]``/``[PASS]``/``[FAIL]``) so CI
logs stay readable on Windows, macOS and Linux.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from infra.workspace.lock import DEFAULT_OPERATION, is_stale, lock_status
from infra.workspace.manager import (
    TEMPLATES,
    Workspace,
    WorkspaceError,
    check_workspace,
    compile_project,
    find_workspace,
    init_workspace,
    load_workspace,
    project_status,
)

workspace_app = typer.Typer(
    help="Manage multi-project workspaces (infra-workspace.yaml).",
    no_args_is_help=True,
)


def _load_ws() -> Workspace:
    """Load the manifest from cwd or exit 1 with a readable error."""
    from rich.console import Console

    try:
        return load_workspace(find_workspace())
    except WorkspaceError as exc:
        Console().print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@workspace_app.command("init")
def workspace_init(
    template: str = typer.Option(
        "basic",
        "--template",
        "-t",
        help=f"Starter template: {' | '.join(TEMPLATES)}",
    ),
) -> None:
    """Create infra-workspace.yaml and starter files in the current directory."""
    from rich.console import Console

    console = Console()
    cwd = Path.cwd()
    try:
        written = init_workspace(cwd, template)
    except WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    typer.echo(f"Workspace initialized from template '{template}' [OK]")
    for path in written:
        typer.echo(f"  + {path.relative_to(cwd)}")
    typer.echo("Next: infra workspace list")


@workspace_app.command("list")
def workspace_list() -> None:
    """List all workspace projects with their validation status."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    ws = _load_ws()
    table = Table(title=f"Workspace projects ({ws.root})")
    table.add_column("Project", style="cyan")
    table.add_column("Path")
    table.add_column("Target")
    table.add_column("Status")
    colors = {
        "valid": "green",
        "invalid": "red",
        "parse-error": "red",
        "missing": "yellow",
    }
    for spec in ws.projects:
        status = project_status(ws, spec)
        marker = "[OK]" if status == "valid" else "[FAIL]"
        table.add_row(
            spec.name,
            spec.path,
            spec.target,
            f"[{colors[status]}]{marker} {status}[/{colors[status]}]",
        )
    console.print(table)


@workspace_app.command("check")
def workspace_check(
    environment: Optional[str] = typer.Option(
        None,
        "--environment",
        "-e",
        "--env",
        help="Environment overlay applied to every project.",
    ),
) -> None:
    """Validate all projects (semantics + workspace policies); exit 1 on any failure."""
    ws = _load_ws()
    reports = check_workspace(ws, environment)
    failures = 0
    for report in reports:
        if report.ok:
            typer.echo(
                f"[PASS] {report.name} "
                f"({report.path}, target: {report.target})"
            )
            continue
        failures += 1
        typer.echo(
            f"[FAIL] {report.name} "
            f"({report.path}, target: {report.target})"
        )
        for err in report.errors:
            typer.echo(f"    - {err}")
        for violation in report.violations:
            where = (
                f" [{violation.resource}]"
                if getattr(violation, "resource", None)
                else ""
            )
            typer.echo(
                f"    - [{violation.code}] policy violation{where}: "
                f"{violation.message}"
            )
    if failures:
        from rich.console import Console

        Console().print(
            f"[red]{failures} of {len(reports)} project(s) failed.[/red]"
        )
        raise typer.Exit(code=1)
    typer.echo(f"All {len(reports)} project(s) passed [OK]")


@workspace_app.command("compile")
def workspace_compile(
    project: Optional[str] = typer.Option(
        None, "--project", "-p", help="Compile only this project."
    ),
    output: Path = typer.Option(
        Path("./workspace-out"), "--output", "-o", help="Base output directory."
    ),
    environment: Optional[str] = typer.Option(
        None,
        "--environment",
        "-e",
        "--env",
        help="Environment overlay applied to every project.",
    ),
) -> None:
    """Compile every project (or --project NAME) into OUTPUT_DIR/<name>/."""
    from rich.console import Console

    console = Console()
    ws = _load_ws()
    if project is not None:
        try:
            specs = [ws.project(project)]
        except WorkspaceError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
    else:
        specs = list(ws.projects)

    failures = 0
    for spec in specs:
        try:
            files = compile_project(ws, spec, environment)
        except WorkspaceError as exc:
            failures += 1
            typer.echo(f"[FAIL] {exc}")
            continue
        dest = output / spec.name
        for name, content in sorted(files.items()):
            path = dest / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.lstrip("\ufeff"), encoding="utf-8")
        typer.echo(
            f"[OK] {spec.name}: {len(files)} file(s) written to {dest} "
            f"(target: {spec.target})"
        )
    if failures:
        console.print(f"[red]{failures} project(s) failed to compile.[/red]")
        raise typer.Exit(code=1)


@workspace_app.command("unlock")
def workspace_unlock(
    project: str = typer.Argument(..., help="Project whose lock to remove."),
    operation: str = typer.Option(
        DEFAULT_OPERATION,
        "--operation",
        "-o",
        help="Operation name of the lock (e.g. deploy).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Remove the lock even if the owning process is alive.",
    ),
) -> None:
    """Remove a stale deploy lock under ./.infra-state/<project>/locks/.

    Works directly on the state directory (no workspace manifest needed).
    A lock held by a live process is refused unless ``--force`` is given.
    """
    from infra.workspace.lock import lock_path

    state_root = Path.cwd() / ".infra-state"
    path = lock_path(state_root, project, operation)
    info = lock_status(state_root, project, operation)
    if info is None and not path.exists():
        typer.echo(f"No '{operation}' lock for project '{project}' found.")
        return
    if info is not None and not is_stale(info) and not force:
        from rich.console import Console

        Console(stderr=True).print(
            f"[red]Project '{project}' is locked by a live process[/red] "
            f"(pid {info.pid} on {info.hostname}, since {info.timestamp}). "
            "Use --force only if you are sure that process is gone."
        )
        raise typer.Exit(code=1)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    typer.echo(
        f"[OK] Removed '{operation}' lock for project '{project}'"
        + (" (forced)." if force else ".")
    )


__all__: List[str] = ["workspace_app"]
