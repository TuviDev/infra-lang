"""`infra doctor` — diagnose the user's local environment."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
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


def doctor() -> None:
    """Check the user's environment for tools Infra Lang needs and can use."""
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
