"""`infra up` / `infra down` — direct execution of compiled infrastructure.

Compiles a `.infra` file to a target backend, then applies (up) or removes
(down) the resulting resources using the target CLI tool (kubectl, docker
compose, helm). A `--dry-run` flag prints the commands that would run without
executing them. Missing tools produce a clear error pointing at `infra doctor`.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from infra.analyzer.validator import SemanticValidator
from infra.backends import get_backend
from infra.parser import parse_file

#: Tools required per target, plus how to probe them.
_TARGET_TOOL = {
    "kubernetes": "kubectl",
    "k8s": "kubectl",
    "compose": "docker",
    "docker": "docker",
    "helm": "helm",
}


def _have_tool(binary: str) -> bool:
    """Return True if *binary* is on PATH."""
    return shutil.which(binary) is not None


def _tool_hint(binary: str) -> str:
    return (
        f"[red]Required tool '{binary}' was not found on PATH.[/red]\n"
        "Run `infra doctor` to inspect your environment, then install the "
        "missing tool or activate the right virtualenv."
    )


def _run(
    cmd: List[str], *, cwd: Optional[Path] = None
) -> subprocess.CompletedProcess[Any]:
    """Run a command, returning the CompletedProcess (utf-8 safe)."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=str(cwd) if cwd else None,
    )


def _compile_files(infra_path: Path, target: str) -> Dict[str, str]:
    """Compile *infra_path* to *target* and return {filename: content}."""
    from rich.console import Console

    console = Console()
    program = parse_file(infra_path)
    vresult = SemanticValidator().validate(program)
    if not vresult.is_valid:
        for e in vresult.errors:
            loc = e.location
            pos = f"{loc.file}:{loc.line}:{loc.column}" if loc else "?"
            console.print(f"[red]error[{e.code}] {pos}: {e.message}[/red]")
        raise typer.Exit(code=1)
    backend = get_backend(target)
    return backend.compile(program).files


def _write_to_temp(files: Dict[str, str]) -> Path:
    """Write compiled files into a temp dir, return the dir."""
    tmp = Path(tempfile.mkdtemp(prefix="infra-up-"))
    for name, content in files.items():
        dest = tmp / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content.lstrip("\ufeff"), encoding="utf-8")
    return tmp


def _build_commands(target: str, tmp: Path, infra_path: Path) -> List[List[str]]:
    """Build the CLI commands to apply resources for *target*."""
    infra_name = infra_path.stem
    if target in ("kubernetes", "k8s"):
        return [["kubectl", "apply", "-f", str(tmp / "infra.yaml")]]
    if target in ("compose", "docker"):
        return [
            ["docker", "compose", "-f", str(tmp / "docker-compose.yml"), "up", "-d"]
        ]
    if target == "helm":
        # helm expects a chart directory; derive it from the generated files
        chart_dir = tmp / infra_name
        if not chart_dir.exists():
            # fall back to any single chart dir under tmp
            chart_dir = next(
                (p for p in tmp.iterdir() if p.is_dir()), tmp / "app"
            )
        return [
            ["helm", "upgrade", "--install", infra_name, str(chart_dir)]
        ]
    raise typer.Exit(code=1)


def _build_down_commands(
    target: str, tmp: Path, infra_path: Path
) -> List[List[str]]:
    """Build the CLI commands to remove resources for *target*."""
    infra_name = infra_path.stem
    if target in ("kubernetes", "k8s"):
        return [["kubectl", "delete", "-f", str(tmp / "infra.yaml")]]
    if target in ("compose", "docker"):
        return [
            ["docker", "compose", "-f", str(tmp / "docker-compose.yml"), "down", "-v"]
        ]
    if target == "helm":
        return [["helm", "uninstall", infra_name]]
    raise typer.Exit(code=1)


def up(
    file: Path = typer.Argument(..., help=".infra file to deploy"),
    target: str = typer.Option(
        "kubernetes", "--target", "-t", help="kubernetes | compose | helm"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print commands only"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n"),
) -> None:
    """Compile and apply a .infra file to the target platform."""
    from rich.console import Console

    console = Console()
    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    tool = _TARGET_TOOL.get(target)
    if tool is None:
        console.print(
            f"[red]Unsupported target '{target}'. "
            "Use kubernetes, compose, or helm.[/red]"
        )
        raise typer.Exit(code=1)
    if not dry_run and not _have_tool(tool):
        console.print(_tool_hint(tool))
        raise typer.Exit(code=1)

    files = _compile_files(file, target)
    tmp = _write_to_temp(files)
    cmds = _build_commands(target, tmp, file)
    if namespace and target in ("kubernetes", "k8s"):
        cmds = [c + ["-n", namespace] for c in cmds]

    for cmd in cmds:
        console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
        if dry_run:
            continue
        result = _run(cmd)
        if result.returncode != 0:
            console.print(
                f"[red]Command failed (exit {result.returncode}):[/red] {' '.join(cmd)}"
            )
            if result.stderr:
                console.print(result.stderr.strip())
            raise typer.Exit(code=result.returncode)
        if result.stdout:
            console.print(result.stdout.strip())

    if dry_run:
        console.print(
            "[green]Dry run — no resources were applied.[/green] "
            "Re-run without --dry-run to deploy."
        )
    else:
        console.print(f"[green]Applied {file.name} via {tool}.[/green]")


def down(
    file: Path = typer.Argument(..., help=".infra file whose resources to remove"),
    target: str = typer.Option(
        "kubernetes", "--target", "-t", help="kubernetes | compose | helm"
    ),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n"),
) -> None:
    """Remove resources previously applied from a .infra file."""
    from rich.console import Console

    console = Console()
    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    tool = _TARGET_TOOL.get(target)
    if tool is None:
        console.print(
            f"[red]Unsupported target '{target}'. "
            "Use kubernetes, compose, or helm.[/red]"
        )
        raise typer.Exit(code=1)
    if not _have_tool(tool):
        console.print(_tool_hint(tool))
        raise typer.Exit(code=1)

    files = _compile_files(file, target)
    tmp = _write_to_temp(files)
    cmds = _build_down_commands(target, tmp, file)
    if namespace and target in ("kubernetes", "k8s"):
        cmds = [c + ["-n", namespace] for c in cmds]

    for cmd in cmds:
        console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
        result = _run(cmd)
        if result.returncode != 0:
            console.print(
                f"[red]Command failed (exit {result.returncode}):[/red] {' '.join(cmd)}"
            )
            if result.stderr:
                console.print(result.stderr.strip())
            raise typer.Exit(code=result.returncode)
        if result.stdout:
            console.print(result.stdout.strip())

    console.print(f"[green]Removed resources for {file.name} via {tool}.[/green]")
