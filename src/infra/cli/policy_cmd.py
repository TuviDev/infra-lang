"""`infra policy-check` — evaluate a .infra file against a YAML policy.

Teams keep declarative rules (budgets, no hardcoded secret env vars,
forbidden image tags) in ``infra-policy.yaml`` and enforce them in CI:

.. code-block:: bash

    infra policy-check app.infra --policy infra-policy.yaml
    infra policy-check app.infra            # auto-discovers ./infra-policy.yaml
    infra policy-check app.infra -f json    # machine-readable

Exit codes: ``0`` — policy passed; ``1`` — violations found or the file /
policy could not be read; ``2`` — usage errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer

if TYPE_CHECKING:  # pragma: no cover
    from infra.policy.engine import PolicyResult


#: Valid values for the ``--format`` option.
_FORMATS = ("text", "json")

#: Policy file name auto-discovered in the current directory.
DEFAULT_POLICY_NAME = "infra-policy.yaml"


def _render_text(result: PolicyResult) -> str:
    lines = [
        f"policy-check: {result.source} "
        f"({result.rules_checked} rule(s) from {result.policy})"
    ]
    for v in result.violations:
        where = f" [{v.resource}]" if v.resource else ""
        lines.append(f"[FAIL] {v.code} (rule '{v.rule_id}'){where}: {v.message}")
    if result.passed:
        lines.append("[OK] policy passed — no violations.")
    return "\n".join(lines)


def policy_check_cmd(
    file: Path = typer.Argument(..., help=".infra file to check"),
    policy: Optional[Path] = typer.Option(
        None,
        "--policy",
        "-p",
        help=f"YAML policy file (default: ./{DEFAULT_POLICY_NAME} if present).",
    ),
    output_format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text | json"
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
) -> None:
    """Check a .infra file against declarative team policy rules."""
    from rich.console import Console

    from infra.parser import parse_file
    from infra.policy.engine import (
        PolicyError,
        PolicyResult,
        evaluate_policy,
        load_policy,
    )

    console = Console(stderr=True)

    fmt = output_format.lower()
    if fmt not in _FORMATS:
        console.print(
            f"[red]Unknown format '{output_format}'.[/red] "
            "Valid formats: text, json"
        )
        raise typer.Exit(code=1)

    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    policy_path = policy
    if policy_path is None:
        candidate = Path.cwd() / DEFAULT_POLICY_NAME
        if candidate.exists():
            policy_path = candidate
        else:
            console.print(
                f"[red]No policy file.[/red] Pass --policy <path> or create "
                f"./{DEFAULT_POLICY_NAME}."
            )
            raise typer.Exit(code=2)
    if not policy_path.exists():
        console.print(f"[red]Policy file not found:[/red] {policy_path}")
        raise typer.Exit(code=1)

    try:
        loaded = load_policy(policy_path)
    except PolicyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    from infra.cli.compile import _apply_environment

    try:
        program = _apply_environment(parse_file(file), environment or "")
    except typer.Exit:
        raise
    except Exception as exc:  # parse errors et al.
        console.print(f"[red]policy-check failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    violations = evaluate_policy(program, loaded)
    result = PolicyResult(
        source=str(file),
        policy=str(policy_path),
        rules_checked=len(loaded.rules),
        violations=violations,
    )

    if fmt == "json":
        typer.echo(json.dumps(result.to_dict(), indent=2))
    else:
        typer.echo(_render_text(result))

    if not result.passed:
        console.print(
            f"[FAIL] policy violated: {len(violations)} violation(s)."
        )
        raise typer.Exit(code=1)
