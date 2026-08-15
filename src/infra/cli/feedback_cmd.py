"""
infra feedback — manage opt-in anonymous error reporting.

Usage:
  infra feedback            # show status
  infra feedback --on       # enable (project config)
  infra feedback --off      # disable (project config)
"""

from __future__ import annotations

from pathlib import Path

import typer

from infra.config import PROJECT_CONFIG_NAME, write_config
from infra.feedback import feedback_status


def _project_config_path() -> Path:
    # Prefer a project-scoped config; fall back to user config location.
    if Path(PROJECT_CONFIG_NAME).exists():
        return Path(PROJECT_CONFIG_NAME)
    import infra.config as config

    return config.USER_CONFIG_PATH  # resolved at call time so tests can patch it


def feedback_cmd(
    on: bool = typer.Option(False, "--on", help="Enable feedback"),
    off: bool = typer.Option(False, "--off", help="Disable feedback"),
    project: bool = typer.Option(
        False, "--project", help="Write to project config (default)"
    ),
) -> None:
    """Show or change the opt-in anonymous error reporting setting."""
    if on and off:
        typer.echo("error: use only one of --on / --off", err=True)
        raise typer.Exit(1)

    if on or off:
        enabled = bool(on)
        path = _project_config_path()
        write_config(path, enabled)
        typer.echo(f"feedback {'enabled' if enabled else 'disabled'} ({path})")
        return

    status = feedback_status()
    typer.echo(f"feedback enabled : {status['enabled']}")
    typer.echo(f"config source    : {status['source']}")
    typer.echo(f"collector        : {status['collector']}")
    typer.echo(f"privacy          : {status['privacy']}")
