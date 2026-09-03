"""`infra deploy` / `infra rollback` — deployments with rollout safety.

**Default is DRY-RUN:** the command compiles and prints a structured plan
(resources, estimated cost from ``cost.py``, risk indicators from
``security.py``) plus the exact tool commands. ``--apply`` executes them
through the offline-testable engine, verifies the rollout and — on
failure — auto-rolls back to the previous good snapshot.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import typer

from infra.deploy.engine import (
    PLANNED,
    RESTORED,
    SUCCESS,
    DeployRecord,
    apply_command_set,
    canonical_target,
    execute_deploy,
    execute_rollback,
    have_tool,
    list_history,
    target_tool,
)

if TYPE_CHECKING:  # pragma: no cover
    from rich.console import Console

    from infra.backends.base import Backend
    from infra.parser import ast_nodes as n


def get_backend(name: str, **opts: Any) -> Backend:
    """Lazy proxy for :func:`infra.backends.get_backend`.

    Keeps the CLI startup cost at zero (no analyzer/backend import until a
    command actually runs) while preserving this module attribute as the
    single monkey-patch point used by the test-suite.
    """
    from infra.backends import get_backend as _real

    return _real(name, **opts)


def _prepare_program(file: Path, environment: Optional[str]) -> n.Program:
    """Parse, overlay and semantically validate *file* (exits on error)."""
    from rich.console import Console

    from infra.parser import parse_file

    console = Console()
    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)
    try:
        program = parse_file(file)
    except Exception as exc:
        detail = str(exc).splitlines()[0] if str(exc) else "parse error"
        console.print(f"[red]Cannot parse {file}:[/red] {detail}")
        raise typer.Exit(code=1) from exc

    from typing import cast

    from infra.cli.compile import _apply_environment
    from infra.parser import ast_nodes as n

    program = cast(n.Program, _apply_environment(program, environment or ""))

    from infra.analyzer.validator import SemanticValidator

    vresult = SemanticValidator().validate(program)
    if not vresult.is_valid:
        for e in vresult.errors:
            loc = e.location
            pos = f"{loc.file}:{loc.line}:{loc.column}" if loc else "?"
            console.print(f"[red]error[{e.code}] {pos}: {e.message}[/red]")
        raise typer.Exit(code=1)
    return program


def _service_names(program: n.Program) -> List[str]:
    from infra.parser import ast_nodes as n

    return [
        stmt.name for stmt in program.statements if isinstance(stmt, n.ServiceDef)
    ]


def _collect_resources(program: n.Program) -> List[Tuple[str, str]]:
    from infra.parser import ast_nodes as n

    kinds = (
        (n.ServiceDef, "service"),
        (n.DatabaseDef, "database"),
        (n.CacheDef, "cache"),
        (n.QueueDef, "queue"),
        (n.StorageDef, "storage"),
    )
    return [
        (kind, stmt.name)
        for stmt in program.statements
        for cls, kind in kinds
        if isinstance(stmt, cls)
    ]


def _print_plan(
    console: Console,
    *,
    file: Path,
    canonical: str,
    program: n.Program,
    files: Dict[str, str],
) -> None:
    from infra.analyzer.cost import estimate_cost
    from infra.analyzer.security import SecurityChecker

    resources = _collect_resources(program)
    estimate = estimate_cost(program)
    findings = SecurityChecker().check(program)
    console.print(
        f"[bold]Deployment plan[/bold] for [cyan]{file.name}[/cyan] "
        f"(target: {canonical})"
    )
    console.print(f"  resources: {len(resources)}")
    for kind, name in resources:
        console.print(f"    - {kind} [cyan]{name}[/cyan]")
    console.print(
        f"  estimated monthly cost: [green]${estimate.total_monthly_usd:.2f}"
        f"[/green]"
    )
    if findings:
        console.print(f"  risk indicators: {len(findings)} warning(s)")
        from rich.markup import escape

        for finding in findings:
            console.print(
                f"    - {escape('[' + getattr(finding, 'code', '?') + ']')} "
                f"{escape(str(finding.message))}"
            )
    else:
        console.print("  risk indicators: none")
    console.print(f"  files to apply: {', '.join(sorted(files))}")
    commands = apply_command_set(canonical, Path("<manifest-dir>"), file.stem)
    console.print("  commands:")
    for cmd, cwd in commands:
        suffix = f"  (cwd: {cwd})" if cwd else ""
        print(f"    $ {' '.join(cmd)}{suffix}")


def _record_and_exit(console: Console, record: DeployRecord) -> None:
    status_color = {
        SUCCESS: "green",
        RESTORED: "green",
        PLANNED: "cyan",
    }.get(record.status, "red")
    from rich.markup import escape

    console.print(
        f"[{status_color}]revision {record.revision}: {record.status}"
        f"[/{status_color}] — {escape(record.message)}"
    )
    for step in record.steps:
        rc = "?" if step.returncode is None else step.returncode
        print(f"    $ {step.label}  → rc={rc}")
    if record.status in (SUCCESS, RESTORED):
        return
    raise typer.Exit(code=1)


def deploy(
    file: Path = typer.Argument(..., help=".infra file to deploy"),
    target: str = typer.Option(
        "kubernetes",
        "--target",
        "-t",
        help="Deploy target: compose | kubernetes (k8s) | helm | terraform",
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="Execute the deployment (default: print the plan only)",
    ),
    force: bool = typer.Option(
        False, "--force", help="Alias for --apply"
    ),
    timeout: int = typer.Option(
        120, "--timeout", help="Rollout timeout per service in seconds"
    ),
    auto_rollback: bool = typer.Option(
        True,
        "--auto-rollback/--no-auto-rollback",
        help="Roll back automatically when the rollout fails",
    ),
) -> None:
    """Deploy a .infra file (dry-run plan by default, --apply to execute)."""
    from rich.console import Console

    console = Console()
    program = _prepare_program(file, environment)
    try:
        canonical = canonical_target(target)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if (apply or force) and not have_tool(target_tool(canonical)):
        console.print(
            f"[red]Required tool '{target_tool(canonical)}' was not found "
            "on PATH.[/red]"
        )
        raise typer.Exit(code=1)

    try:
        files = get_backend(canonical).compile(program).files
    except Exception as exc:
        console.print(f"[red]Compilation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    project = file.stem
    state_root = file.parent / ".infra-state"

    if not (apply or force):
        _print_plan(
            console, file=file, canonical=canonical, program=program,
            files=files,
        )
        record = execute_deploy(
            project=project,
            target=canonical,
            files=files,
            service_names=_service_names(program),
            state_root=state_root,
            environment=environment or "",
            apply=False,
        )
        console.print(
            "[green]Plan recorded (dry-run).[/green] Re-run with --apply "
            f"to deploy revision {record.revision}."
        )
        return

    record = execute_deploy(
        project=project,
        target=canonical,
        files=files,
        service_names=_service_names(program),
        state_root=state_root,
        environment=environment or "",
        timeout=timeout,
        auto_rollback=auto_rollback,
    )
    _record_and_exit(console, record)


def rollback(
    file: Path = typer.Argument(..., help=".infra file whose stack to roll back"),
    to_revision: Optional[str] = typer.Option(
        None, "--to-revision", help="Revision id to restore (see history)"
    ),
    target: str = typer.Option(
        "kubernetes",
        "--target",
        "-t",
        help="Deploy target: compose | kubernetes (k8s) | helm | terraform",
    ),
    timeout: int = typer.Option(
        120, "--timeout", help="Restore timeout in seconds"
    ),
) -> None:
    """Show deploy history, or restore a previous revision (--to-revision)."""
    from rich.console import Console

    console = Console()
    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)
    try:
        canonical = canonical_target(target)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    project = file.stem
    state_root = file.parent / ".infra-state"

    if to_revision is None:
        history = list_history(state_root, project)
        if not history:
            console.print(
                f"[yellow]No deploy history yet for '{project}'.[/yellow]"
            )
            return
        console.print(f"[bold]Deploy history[/bold] for {project}:")
        from rich.markup import escape

        for record in reversed(history):
            console.print(
                f"  [cyan]{record.revision}[/cyan] {record.timestamp} "
                f"{record.target} {escape('[' + record.status + ']')} "
                f"{escape(record.message)}"
            )
        console.print(
            "Roll back with: infra rollback "
            f"{file.name} --to-revision <REVISION_ID>"
        )
        return

    if not have_tool(target_tool(canonical)):
        console.print(
            f"[red]Required tool '{target_tool(canonical)}' was not found "
            "on PATH.[/red]"
        )
        raise typer.Exit(code=1)
    try:
        record = execute_rollback(
            state_root=state_root,
            project=project,
            target=canonical,
            revision=to_revision,
            timeout=timeout,
        )
    except LookupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    _record_and_exit(console, record)


__all__ = ["deploy", "rollback"]
