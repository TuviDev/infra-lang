"""`infra compliance` — audit-friendly SOC 2 / CIS compliance reports.

.. code-block:: bash

    infra compliance app.infra                  # all standards, text
    infra compliance app.infra --standard soc2  # SOC 2 only
    infra compliance app.infra -f markdown -o report.md

The report lists every control as ``[PASS]``/``[FAIL]`` together with the
norm IDs, the triggering SEC*/REL* error codes, file locations and fix
recommendations, plus an overall **Compliance Score**
(``passed / total * 100``). Exit code is ``0`` when every control passes
and ``1`` otherwise (also on usage/parse errors).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer

if TYPE_CHECKING:  # pragma: no cover
    from infra.compliance.scanner import ComplianceReport


#: Values accepted by the ``--format`` option.
_FORMATS = ("text", "markdown", "json")


def _render_text(report: ComplianceReport) -> str:
    from infra.compliance.mappings import STANDARD_TITLES

    lines = [
        f"infra compliance report: {report.file}",
        f"standard: {STANDARD_TITLES[report.standard]}",
        "",
    ]
    for result in report.results:
        control = result.control
        count = len(result.violations)
        if result.passed:
            lines.append(f"[PASS] {control.control_id} {control.title}")
        else:
            lines.append(
                f"[FAIL] {control.control_id} {control.title} "
                f"({count} violation{'s' if count != 1 else ''})"
            )
            for v in result.violations:
                lines.append(f"    - [{v.code}] {v.message} @ {v.location}")
                if v.recommendation:
                    lines.append(f"      fix: {v.recommendation}")
    lines.append("")
    lines.append(
        f"Compliance score: {report.score:.1f}% "
        f"({report.passed}/{report.total} controls passed)"
    )
    return "\n".join(lines)


def _render_markdown(report: ComplianceReport) -> str:
    from infra.compliance.mappings import STANDARD_TITLES

    lines = [
        "# Compliance Report",
        "",
        f"- **File:** `{report.file}`",
        f"- **Standard:** {STANDARD_TITLES[report.standard]}",
        f"- **Compliance score:** {report.score:.1f}% "
        f"({report.passed}/{report.total} controls passed)",
        "",
        "| Control | Title | Status | Violations |",
        "|---|---|---|---|",
    ]
    for result in report.results:
        control = result.control
        status = "PASS" if result.passed else "**FAIL**"
        lines.append(
            f"| {control.control_id} | {control.title} | {status} "
            f"| {len(result.violations)} |"
        )
    failed = [r for r in report.results if not r.passed]
    if failed:
        lines.extend(["", "## Violations", ""])
        for result in failed:
            lines.append(f"### {result.control.control_id} — {result.control.title}")
            lines.append("")
            for v in result.violations:
                lines.append(
                    f"- **[{v.code}]** {v.message} (`{v.location}`)"
                )
                if v.recommendation:
                    lines.append(f"  - Fix: {v.recommendation}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compliance(
    file: Path = typer.Argument(..., help=".infra file to audit"),
    standard: str = typer.Option(
        "all", "--standard", "-s", help="Standard: soc2 | cis | all"
    ),
    output_format: str = typer.Option(
        "text", "--format", "-f", help="Report format: text | markdown | json"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write the report to this file"
    ),
) -> None:
    """Audit a .infra file against SOC 2 / CIS controls."""
    from rich.console import Console

    from infra.compliance.mappings import STANDARDS
    from infra.compliance.scanner import scan_file

    console = Console(stderr=True)

    std = standard.lower()
    if std not in STANDARDS:
        console.print(
            f"[red]Unknown standard '{standard}'.[/red] "
            f"Valid: {', '.join(STANDARDS)}"
        )
        raise typer.Exit(code=1)

    fmt = output_format.lower()
    if fmt not in _FORMATS:
        console.print(
            f"[red]Unknown format '{output_format}'.[/red] "
            f"Valid: {', '.join(_FORMATS)}"
        )
        raise typer.Exit(code=1)

    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    try:
        report = scan_file(file, std)
    except Exception as exc:
        detail = str(exc).splitlines()[0] if str(exc) else "parse error"
        console.print(f"[red]Cannot parse {file}:[/red] {detail}")
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        rendered = json.dumps(report.to_dict(), indent=2) + "\n"
    elif fmt == "markdown":
        rendered = _render_markdown(report)
    else:
        rendered = _render_text(report) + "\n"

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        typer.echo(f"[OK] compliance report written to {output}")
    else:
        typer.echo(rendered, nl=False)

    if report.failed:
        raise typer.Exit(code=1)
