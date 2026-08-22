"""`infra compile` command."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import typer

from infra.analyzer.validator import SemanticValidator
from infra.backends import get_backend
from infra.parser import _parser


def _parse_var_options(var: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for v in var:
        if "=" in v:
            k, _, val = v.partition("=")
            out[k.strip()] = val.strip()
    return out


def compile_program_to_files(path: Path, target: str) -> Dict[str, str]:
    """Parse + validate a single .infra file and return {filename: content}.

    Shared by `infra compile` and `infra up`/`infra down` so the compile +
    semantic-validation path is defined in exactly one place. Raises
    ``typer.Exit(1)`` on invalid input.
    """
    from rich.console import Console

    program = _parser().parse_file(path)
    vresult = SemanticValidator().validate(program)
    if not vresult.is_valid:
        console = Console()
        for e in vresult.errors:
            loc = e.location
            pos = f"{loc.file}:{loc.line}:{loc.column}" if loc else "?"
            console.print(f"[red]error[{e.code}] {pos}: {e.message}[/red]")
        raise typer.Exit(code=1)
    backend = get_backend(target)
    return backend.compile(program).files


def compile(
    files: List[Path] = typer.Argument(..., help=".infra file(s) to compile"),
    target: str = typer.Option(
        "kubernetes",
        "--target",
        "-t",
        help="Backend: kubernetes, compose, terraform, github, helm",
    ),
    output: Path = typer.Option(
        Path("./infra-out"), "--output", "-o", help="Output directory"
    ),
    split: bool = typer.Option(False, "--split", help="One file per resource"),
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Kubernetes namespace"
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", help="Environment name"
    ),
    var: List[str] = typer.Option([], "--var", help="Variable: --var key=value"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print output without writing"
    ),
    watch: bool = typer.Option(
        False, "--watch", "-w", help="Watch for changes and recompile"
    ),
    validate_output: bool = typer.Option(
        False,
        "--validate-output",
        help="Validate generated Kubernetes YAML output",
    ),
) -> None:
    """Compile .infra files to the chosen backend."""
    variables = _parse_var_options(var)
    if watch and files:
        run_watch(
            source_path=Path(files[0]),
            target=target,
            output_dir=Path(output),
            split=split,
            cli_vars=variables,
            dry_run=dry_run,
        )
        return

    backend = get_backend(target, split=split)
    parser = _parser()
    total = 0
    issues: List[str] = []
    for f in files:
        program = parser.parse_file(f)
        vresult = SemanticValidator().validate(program)
        if not vresult.is_valid:
            for e in vresult.errors:
                loc = e.location
                pos = f"{loc.file}:{loc.line}:{loc.column}" if loc else "?"
                typer.echo(f"error[{e.code}] {pos}: {e.message}")
            typer.echo(f"Compilation aborted: {len(vresult.errors)} error(s)")
            raise typer.Exit(code=1)
        compiled = backend.compile(program, cli_vars=variables)
        if validate_output and target == "kubernetes":
            from infra.validation.k8s_validator import KubernetesOutputValidator
            from infra.validation.schema_validator import validate_compiled_output

            validator = KubernetesOutputValidator()
            for name, content in compiled.files.items():
                for vissue in validator.validate(content):
                    issues.append(f"{f.name}/{name}: {vissue}")
            for sissue in validate_compiled_output(compiled.files):
                if sissue.severity == "error":
                    issues.append(
                        f"{f.name}: error {sissue.kind}/{sissue.name}: "
                        f"{sissue.field}: {sissue.message}"
                    )
        if dry_run:
            for name, content in compiled.files.items():
                typer.echo(f"=== {name} ===")
                typer.echo(content)
        else:
            out_dir = output
            out_dir.mkdir(parents=True, exist_ok=True)
            for name, content in compiled.files.items():
                dest = out_dir / name
                # Helm emits nested paths (e.g. <chart>/templates/...); ensure
                # the parent directory exists before writing.
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Defensive: strip any UTF-8 BOM from the text before writing.
                # Helm's Go YAML parser rejects a file that starts with a BOM
                # (`yaml: invalid leading UTF-8 octet`). encoding="utf-8" writes
                # UTF-8; this guards against a stray \ufeff in generated content.
                dest.write_text(content.lstrip("\ufeff"), encoding="utf-8")
                total += 1
    if issues:
        for issue in issues:
            typer.echo(f"validation error: {issue}")
        typer.echo(f"Validation failed with {len(issues)} issue(s)")
        raise typer.Exit(code=1)
    if not dry_run:
        typer.echo(f"✅ Compiled {total} files to {output}/")


# --------------------------------------------------------------------------- #
# Watch mode
# --------------------------------------------------------------------------- #


def _collect_watched_files(source_path: Path, program: Any) -> Set[Path]:
    files: Set[Path] = {source_path.resolve()}
    try:
        from infra.parser import ast_nodes as n

        for imp in program.imports:
            if not isinstance(imp, n.Import):
                continue
            raw = (imp.path or "").strip('"').strip("'")
            candidate = source_path.parent / raw
            if candidate.exists():
                files.add(candidate.resolve())
    except Exception:
        pass
    return files


def _compile_once_watch(
    source_path: Path,
    target: str,
    output_dir: Path,
    split: bool,
    cli_vars: Dict[str, str],
    dry_run: bool,
    console: Any,
) -> tuple[bool, float, Set[Path]]:
    import time

    from infra.analyzer.validator import SemanticValidator
    from infra.backends import get_backend
    from infra.parser import parse_file

    t0 = time.perf_counter()
    watched: Set[Path] = {source_path.resolve()}
    try:
        program = parse_file(source_path)
        watched = _collect_watched_files(source_path, program)
        result = SemanticValidator().validate(program)
        if not result.is_valid:
            for e in result.errors[:5]:
                console.print(f"  [red]{e.code}[/red]: {e.message}")
            elapsed = (time.perf_counter() - t0) * 1000
            return False, elapsed, watched
        backend = get_backend(target)
        compiled = backend.compile(program, split=split, cli_vars=cli_vars)
        if not dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
            for fname, content in compiled.files.items():
                out = output_dir / fname
                out.parent.mkdir(parents=True, exist_ok=True)
                # strip a stray UTF-8 BOM so Go YAML (helm) accepts the file
                out.write_text(content.lstrip("\ufeff"), encoding="utf-8")
        elapsed = (time.perf_counter() - t0) * 1000
        return True, elapsed, watched
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        console.print(f"  [red]Error:[/red] {exc}")
        return False, elapsed, watched


def run_watch(
    source_path: Path,
    target: str,
    output_dir: Path,
    split: bool,
    cli_vars: Dict[str, str],
    dry_run: bool,
) -> None:
    import threading
    import time

    from rich.console import Console
    from watchdog.events import FileModifiedEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    console = Console()
    recompile = threading.Event()
    watched: Set[Path] = {source_path.resolve()}

    class Handler(FileSystemEventHandler):
        def on_modified(self, event: Any) -> None:
            if isinstance(event, FileModifiedEvent):
                src = event.src_path
                if isinstance(src, bytes):
                    src = src.decode("utf-8", errors="replace")
                p = Path(src).resolve()
                if p in watched:
                    recompile.set()

    console.print(
        f"\n[bold cyan]Infra Watch[/bold cyan] "
        f"[dim]-[/dim] "
        f"[green]{source_path.name}[/green] "
        f"-> [yellow]{target}[/yellow]"
    )

    ok, ms, watched = _compile_once_watch(
        source_path, target, output_dir, split, cli_vars, dry_run, console
    )
    ts = time.strftime("%H:%M:%S")
    icon = "✅" if ok else "❌"
    console.print(
        f"[dim]{ts}[/dim] {icon} "
        f"{'Compiled' if ok else 'Error'} "
        f"[dim]({ms:.0f}ms)[/dim]"
    )

    handler = Handler()
    observer = Observer()
    observer.schedule(handler, str(source_path.parent), recursive=True)
    observer.start()

    try:
        while True:
            triggered = recompile.wait(timeout=0.3)
            if triggered:
                recompile.clear()
                ok, ms, watched = _compile_once_watch(
                    source_path, target, output_dir, split, cli_vars, dry_run, console
                )
                ts = time.strftime("%H:%M:%S")
                icon = "✅" if ok else "❌"
                console.print(
                    f"[dim]{ts}[/dim] {icon} "
                    f"Recompiled [dim]({ms:.0f}ms)[/dim]"
                )
    except KeyboardInterrupt:
        console.print("\n[dim]Watch mode stopped.[/dim]")
    finally:
        observer.stop()
        observer.join()
