"""`infra sbom` — offline-first SBOM generation for .infra files (v0.9.0).

Formats: SPDX 2.3 JSON, CycloneDX 1.5 JSON, markdown table, plain text.
The optional ``--registry-check`` is a best-effort live probe (injectable
fetcher inside ``infra.sbom.generator``; CI tests always mock it).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from infra.explain import source_checksum
from infra.parser import parse_file
from infra.sbom import FORMATS
from infra.sbom.generator import (
    add_transitive,
    check_availability,
    collect_components,
    render_sbom,
)


def sbom(
    file: Path = typer.Argument(..., help=".infra file to analyze"),
    output_format: str = typer.Option(
        "markdown",
        "--format",
        "-f",
        help="SBOM format: spdx-json | cyclonedx-json | markdown | text",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write the SBOM to a file instead of stdout"
    ),
    include_transitive: bool = typer.Option(
        False,
        "--include-transitive",
        help="Add best-effort transitive base images (bundled database)",
    ),
    registry_check: bool = typer.Option(
        False,
        "--registry-check",
        help="Best-effort live registry availability probe (needs network)",
    ),
) -> None:
    """Generate a Software Bill of Materials for an .infra file."""
    from rich.console import Console

    console = Console()

    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    fmt = output_format.lower()
    if fmt not in FORMATS:
        console.print(
            f"[red]Unknown format '{output_format}'.[/red] "
            f"Valid formats: {', '.join(FORMATS)}"
        )
        raise typer.Exit(code=1)

    try:
        source = file.read_text(encoding="utf-8")
        program = parse_file(file)
    except Exception as exc:
        detail = str(exc).splitlines()[0] if str(exc) else "parse error"
        console.print(f"[red]Cannot parse {file}:[/red] {detail}")
        raise typer.Exit(code=1) from exc

    components = collect_components(program)
    if include_transitive:
        components = add_transitive(components)

    availability = check_availability(components) if registry_check else None

    # Deterministic for an unchanged input file: timestamp from file mtime
    # (same convention as `infra explain`).
    now = datetime.fromtimestamp(
        file.stat().st_mtime, timezone.utc
    ).isoformat(timespec="seconds")
    report = render_sbom(
        components,
        fmt,
        project=file.stem,
        source_name=file.name,
        checksum=source_checksum(source),
        timestamp=now,
        availability=availability,
    )

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        if not report.endswith("\n"):
            report += "\n"
        output.write_text(report, encoding="utf-8")
        console.print(f"[green]SBOM written to[/green] {output}")
        return
    typer.echo(report)


__all__ = ["sbom"]
