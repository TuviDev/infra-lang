"""`infra diff` command — compare two .infra files or plan against live state.

File-to-file mode (default) compares two on-disk specifications field by
field. ``--live`` mode turns the command into a *plan & preview* (the
``terraform plan`` equivalent): the desired spec from a single ``.infra``
file is compared against the live state of a Kubernetes namespace or a Docker
Compose stack — using strictly read-only probes — and the planned changes are
printed as a colored diff, e.g.::

    ~ service "app":
        replicas: 2 -> 5
        image: "myapi:v1.0" -> "myapi:v1.1"

Exit code is 0 when the live state already matches the spec, 1 when changes
are pending (or the plan could not be computed), and 2 on usage errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from infra.analyzer.drift import STATUS_MISSING, DriftItem, DriftReport
from infra.parser import _parser


def _quote(value: str) -> str:
    """Quote *value* for the plan output, keeping plain numbers bare."""
    return value if value.lstrip("-").isdigit() else f'"{value}"'


def _render_live_plan(
    console: Any, report: DriftReport, source: Path, namespace: str
) -> None:
    """Print a terraform-plan-style preview of the pending live changes."""
    creates: List[DriftItem] = []
    modified: Dict[str, List[DriftItem]] = {}
    for item in report.items:
        if item.status == STATUS_MISSING and item.parameter == "resource":
            creates.append(item)
        else:
            modified.setdefault(item.resource, []).append(item)

    scope = f", namespace={namespace}" if report.target == "k8s" else ""
    console.print(
        f"[bold]{source.name}[/bold]: planned changes against the live "
        f"[cyan]{report.target}[/cyan] state{scope} (read-only preview):"
    )
    console.print()

    for item in creates:
        console.print(
            f'[green]+ service "{item.resource}"[/green] '
            "[green](absent in live state — will be created)[/green]"
        )
    for resource, items in modified.items():
        console.print(f'[yellow]~ service "{resource}":[/yellow]')
        for item in items:
            console.print(
                f"    {item.parameter}: "
                f"{_quote(item.live)} -> {_quote(item.expected)}"
            )
    for name in report.in_sync:
        console.print(f'[dim]= service "{name}" (unchanged)[/dim]')

    if report.has_drift:
        console.print()
        console.print(
            f"[bold]Plan:[/bold] {len(creates)} to create, "
            f"{len(modified)} to change "
            f"({len(report.items)} field change(s) across "
            f"{len(creates) + len(modified)} service(s)); "
            f"{len(report.in_sync)} unchanged."
        )
        console.print(
            "[yellow]Hint: run `infra up <file>` to apply the planned "
            "changes.[/yellow]"
        )
    else:
        console.print(
            "[green]No changes. The live infrastructure matches the "
            "specification.[/green]"
        )


def _live_payload(report: DriftReport, source: Path, namespace: str) -> str:
    """Serialize the live plan as JSON for CI gates."""
    payload: Dict[str, Any] = {
        "source": str(source),
        "namespace": namespace,
        **report.to_dict(),
    }
    return json.dumps(payload, indent=2)


def _diff_live(
    file: Path,
    target: str,
    environment: Optional[str],
    namespace: str,
    format: str,
) -> None:
    """Plan & preview: compare *file* against the live cluster/stack state."""
    from rich.console import Console

    console = Console()

    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    from infra.cli.compile import _apply_environment

    try:
        program = _parser().parse_file(file)
        program = _apply_environment(program, environment or "")
    except typer.Exit:
        raise
    except Exception as exc:  # parse errors
        console.print(f"[red]Plan failed:[/red] {exc}")
        raise typer.Exit(code=1)

    from infra.analyzer.drift import detect_live_drift_program

    report = detect_live_drift_program(program, target=target, namespace=namespace)

    if report.error:
        console.print(f"[red]Live plan failed:[/red] {report.error}")
        raise typer.Exit(code=1)

    if format == "json":
        typer.echo(_live_payload(report, file, namespace))
    else:
        _render_live_plan(console, report, file, namespace)
    raise typer.Exit(code=1 if report.has_drift else 0)


def diff_cmd(
    file1: Path = typer.Argument(
        ..., help="First .infra file (with --live: the desired spec)"
    ),
    file2: Optional[Path] = typer.Argument(
        None, help="Second .infra file (after); not used with --live"
    ),
    format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text, json"
    ),
    only_changes: bool = typer.Option(
        False, "--only-changes", help="Show only changed items"
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Plan & preview: compare the spec against the live "
        "infrastructure (read-only) instead of a second file.",
    ),
    target: str = typer.Option(
        "k8s", "--target", "-t", help="With --live: k8s | compose"
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
    namespace: str = typer.Option(
        "default",
        "--namespace",
        "-n",
        help="Kubernetes namespace for the live comparison (--live).",
    ),
) -> None:
    """Compare two .infra files field by field, or preview live changes."""
    if live:
        if file2 is not None:
            typer.echo(
                "Error: --live compares a single spec file against the live "
                "state; do not pass a second file."
            )
            raise typer.Exit(code=2)
        _diff_live(file1, target, environment, namespace, format)
        return

    if file2 is None:
        typer.echo(
            "Error: missing the second .infra file to compare against "
            "(or pass --live to plan against the live infrastructure)."
        )
        raise typer.Exit(code=2)

    from infra.diff.engine import InfraDiff

    parser = _parser()
    p1 = parser.parse(file1.read_text(encoding="utf-8"), filename=file1.name)
    p2 = parser.parse(file2.read_text(encoding="utf-8"), filename=file2.name)
    result = InfraDiff().diff(p1, p2)

    if format == "json":
        typer.echo(result.format_json())
    else:
        typer.echo(result.format(color=True, only_changes=only_changes))
