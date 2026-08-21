"""`infra doctor` — diagnose the user's local environment."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import typer

from infra.version import __version__

_TOOL_TIMEOUT = 4.0


@dataclass
class Check:
    """A single environment check result."""

    name: str
    ok: bool
    detail: str


def _run(binary: str, args: List[str]) -> Tuple[int, str]:
    """Run a command, return (returncode, stdout+stderr)."""
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=_TOOL_TIMEOUT,
            check=False,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def _tool_version(name: str) -> Optional[str]:
    """Return the first non-empty output line of `<tool> --version`, or None."""
    if shutil.which(name) is None:
        return None
    for args in (["--version"], ["version"]):
        code, out = _run(name, args)
        if code == 0 and out.strip():
            for line in out.splitlines():
                if line.strip():
                    return line.strip()
    return None


def _python_info() -> Tuple[bool, str]:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    return ok, f"{v.major}.{v.minor}.{v.micro}"


def _checks() -> List[Check]:
    checks: List[Check] = []

    py_ok, py_ver = _python_info()
    checks.append(Check("Python", py_ok, py_ver))

    if shutil.which("docker") is None:
        checks.append(Check("Docker", False, "not found"))
    else:
        code, _ = _run("docker", ["info"])
        if code == 0:
            ver = _tool_version("docker")
            detail = f"running ({ver})" if ver else "running"
            checks.append(Check("Docker", True, detail))
        else:
            checks.append(Check("Docker", False, "daemon not running"))

    for binary, label in (
        ("kubectl", "kubectl"),
        ("helm", "helm"),
        ("kind", "kind"),
        ("kubeconform", "kubeconform"),
    ):
        ver = _tool_version(binary)
        checks.append(Check(label, ver is not None, ver if ver else "not found"))

    try:
        import pygls  # noqa: F401

        checks.append(Check("LSP (pygls)", True, "installed"))
    except ImportError:
        hint = "not installed — run pip install 'infra-lang[lsp]'"
        checks.append(Check("LSP (pygls)", False, hint))

    return checks


def _check_drift(
    infra_path: Path,
    out_dir: Path,
    target: str,
) -> None:
    """Run the on-disk drift check and exit with the appropriate code."""
    from rich.console import Console

    console = Console()

    if not infra_path.exists():
        console.print(f"[red]Source file not found:[/red] {infra_path}")
        raise typer.Exit(code=1)

    from infra.analyzer.drift import detect_drift, render_drift

    try:
        result = detect_drift(infra_path, out_dir, target=target)
    except Exception as exc:  # parse errors / unknown backend
        console.print(f"[red]Drift check failed:[/red] {exc}")
        raise typer.Exit(code=1)

    if result.clean:
        console.print(
            "[green]No drift detected. On-disk files match source compilation.[/green]"
        )
        raise typer.Exit(code=0)

    console.print(render_drift(result))
    if result.missing_files:
        console.print(
            "\n[yellow]Hint: run `infra compile <file> --target "
            f"{target} --output {out_dir}` to generate the missing files.[/yellow]"
        )
    raise typer.Exit(code=1)


def _checks_json() -> dict:
    """Return environment checks as a JSON-friendly mapping."""
    from infra.version import __version__

    out = {"version": __version__}
    for c in _checks():
        out[c.name.lower().replace(" ", "_")] = {"installed": c.ok, "detail": c.detail}
    return out


def _check_drift_json(infra_path: Path, out_dir: Path, target: str) -> dict:
    """Run the drift check and return the result as a JSON-serializable dict."""
    from infra.analyzer.drift import detect_drift

    result = detect_drift(infra_path, out_dir, target=target)
    return {
        "has_drift": result.has_drift,
        "modified_files": [
            {"path": name, "diff": diff} for name, diff in result.modified_files
        ],
        "missing_files": result.missing_files,
        "target": result.target,
    }


def doctor(
    check_drift: Optional[Path] = typer.Option(
        None,
        "--check-drift",
        "-d",
        help="Path to a .infra file to check for on-disk drift.",
    ),
    out_dir: Path = typer.Option(
        Path("./infra-out"),
        "--out-dir",
        "-o",
        help="Directory containing generated output (for --check-drift).",
    ),
    target: str = typer.Option(
        "kubernetes",
        "--target",
        "-t",
        help=(
            "Compile target for --check-drift "
            "(kubernetes, compose, terraform, github, helm)."
        ),
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit structured JSON for CI pipelines."
    ),
) -> None:
    """Check the user's environment, or detect on-disk drift with --check-drift."""
    import json as _json

    if check_drift is not None:
        if json_output:
            try:
                payload = _check_drift_json(check_drift, out_dir, target)
            except Exception as exc:
                payload = {"has_drift": True, "error": str(exc),
                           "modified_files": [], "missing_files": []}
            typer.echo(_json.dumps(payload, indent=2))
            raise typer.Exit(code=1 if payload.get("has_drift") else 0)
        _check_drift(check_drift, out_dir, target)

    if json_output:
        typer.echo(_json.dumps(_checks_json(), indent=2))
        return

    checks = _checks()
    typer.echo(f"Infra Lang v{__version__}")
    for c in checks:
        marker = "✓" if c.ok else "✗"
        typer.echo(f"{c.name}: {c.detail} {marker}")

    missing = [c.name for c in checks if not c.ok]
    if missing:
        typer.echo("")
        typer.echo("Missing: " + ", ".join(missing))
        typer.echo("Some commands (compile) work without these; live Kubernetes")
        typer.echo("E2E and backend tooling need them.")
