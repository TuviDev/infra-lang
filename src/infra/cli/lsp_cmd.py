"""
infra lsp — start the language server.

Usage:
  infra lsp                    # stdio mode (default, for editors)
  infra lsp --tcp --port 2087  # TCP mode (for debugging)
"""

from __future__ import annotations

import typer


def lsp_cmd(
    tcp: bool = typer.Option(False, "--tcp", help="Use TCP instead of stdio"),
    host: str = typer.Option("127.0.0.1", "--host", help="TCP host (only with --tcp)"),
    port: int = typer.Option(2087, "--port", help="TCP port (only with --tcp)"),
) -> None:
    try:
        from ..lsp.server import server
    except ImportError:
        typer.echo(
            "Error: pygls not installed.\nRun: pip install 'infra-lang[lsp]'",
            err=True,
        )
        raise typer.Exit(1)

    if tcp:
        server.start_tcp(host, port)
    else:
        server.start_io()
