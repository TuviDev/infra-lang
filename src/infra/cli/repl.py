"""`infra repl` command — interactive REPL."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer

from infra.analyzer.validator import SemanticValidator
from infra.backends import get_backend
from infra.parser import _parser

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    _PT = True
except Exception:  # pragma: no cover
    _PT = False


class InfraREPL:
    def __init__(
        self, target: str = "kubernetes", history_file: Optional[Path] = None
    ) -> None:
        self.target = target
        self.parser = _parser()
        self.history_file = history_file or Path.home() / ".infra_history"
        self.accumulator: list[str] = []
        self.last_ast = None
        self.symbols = None

    def run(self) -> None:
        session: Any = None
        if _PT:
            session = PromptSession(history=FileHistory(str(self.history_file)))
        else:  # pragma: no cover
            session = None
        while True:
            try:
                if session is not None:
                    text = session.prompt("infra> ")
                else:
                    text = input("infra> ")
            except (EOFError, KeyboardInterrupt):
                typer.echo()
                break
            text = text.strip()
            if not text:
                continue
            if text.startswith(":"):
                if self.handle_command(text[1:]):
                    break
                continue
            if self._is_incomplete(text):
                self.accumulator.append(text)
                continue
            line = "\n".join(self.accumulator + [text])
            self.accumulator = []
            self.process_input(line)

    def _is_incomplete(self, text: str) -> bool:
        opens = text.count("{") + text.count("[") + text.count("(")
        closes = text.count("}") + text.count("]") + text.count(")")
        return opens > closes

    def process_input(self, text: str) -> None:
        try:
            program = self.parser.parse(text)
        except Exception as e:
            typer.echo(f"Parse error: {e}")
            return
        result = SemanticValidator().validate(program)
        if not result.is_valid:
            for error_item in result.errors:
                typer.echo(f"Error[{error_item.code}]: {error_item.message}")
            return
        self.symbols = result
        try:
            backend = get_backend(self.target)
            compiled = backend.compile(program)
            for name, content in compiled.files.items():
                typer.echo(f"--- {name} ---")
                lines = content.splitlines()
                typer.echo("\n".join(lines[:20]))
                if len(lines) > 20:
                    typer.echo(f"... ({len(lines) - 20} more lines)")
        except Exception as e:
            typer.echo(f"Compile error: {e}")

    def handle_command(self, cmd: str) -> bool:
        args = cmd.split()
        name = args[0]
        if name in ("quit", "exit"):
            return True
        if name == "help":
            typer.echo(
                "Commands: :help :quit :clear :reset :load FILE :compile :target NAME :show ast|symbols"  # noqa: E501
            )
        elif name == "clear":
            typer.echo("\033c")
        elif name == "reset":
            self.accumulator = []
            self.last_ast = None
            typer.echo("State reset")
        elif name == "load" and len(args) >= 2:
            path = Path(args[1])
            if path.exists():
                self.process_input(path.read_text())
            else:
                typer.echo(f"File not found: {path}")
        elif name == "target" and len(args) >= 2:
            self.target = args[1]
            typer.echo(f"Target set to {self.target}")
        elif name == "show" and len(args) >= 2:
            self._show(args[1])
        return False

    def _show(self, what: str) -> None:
        if what == "ast":
            typer.echo(str(self.last_ast))
        elif what == "symbols":
            if self.symbols:
                typer.echo(
                    f"errors={len(self.symbols.errors)} warnings={len(self.symbols.warnings)}"  # noqa: E501
                )
        else:
            typer.echo("Unknown :show target")


def repl(
    target: str = typer.Option("kubernetes", "--target", help="Backend for preview"),
    history: Optional[Path] = typer.Option(None, "--history", help="History file"),
) -> None:
    """Start an interactive REPL."""
    InfraREPL(target=target, history_file=history).run()
