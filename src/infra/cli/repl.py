"""`infra repl` command — interactive REPL."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import typer

if TYPE_CHECKING:  # pragma: no cover
    from infra.analyzer.validator import ValidationResult

# Lazy prompt_toolkit resolution keeps `infra --help` startup fast: the
# ~90 ms import cost of prompt_toolkit is only paid when the REPL actually
# runs. ``_PT`` uses a tri-state: ``None`` = not yet probed, ``True`` /
# ``False`` = resolved (or forced by tests, which monkeypatch the module
# attributes directly).
PromptSession: Any = None
FileHistory: Any = None
_PT: Optional[bool] = None


def _resolve_pt() -> None:
    """Import prompt_toolkit on first use, setting the module globals."""
    global _PT, PromptSession, FileHistory
    if _PT is not None:
        return
    try:
        from prompt_toolkit import PromptSession as _Session
        from prompt_toolkit.history import FileHistory as _History

        PromptSession, FileHistory = _Session, _History
        _PT = True
    except Exception:  # pragma: no cover
        _PT = False


class InfraREPL:
    def __init__(
        self, target: str = "kubernetes", history_file: Optional[Path] = None
    ) -> None:
        from infra.parser import _parser

        self.target = target
        self.parser = _parser()
        self.history_file = history_file or Path.home() / ".infra_history"
        self.accumulator: list[str] = []
        self.last_ast = None
        self.symbols: Optional[ValidationResult] = None

    def run(self) -> None:
        _resolve_pt()
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
        from infra.analyzer.validator import SemanticValidator
        from infra.backends import get_backend

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
                self.process_input(path.read_text(encoding="utf-8"))
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
