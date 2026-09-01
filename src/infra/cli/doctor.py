"""`infra doctor` — diagnose the user's local environment."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _check_live_drift(infra_path: Path, target: str, namespace: str) -> None:
    """Run the live drift check, print a rich summary table, set exit code."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    if not infra_path.exists():
        console.print(f"[red]Source file not found:[/red] {infra_path}")
        raise typer.Exit(code=1)

    from infra.analyzer.drift import detect_live_drift

    try:
        report = detect_live_drift(infra_path, target=target, namespace=namespace)
    except Exception as exc:  # parse errors
        console.print(f"[red]Drift check failed:[/red] {exc}")
        raise typer.Exit(code=1)

    if report.error:
        console.print(f"[red]Live drift check failed:[/red] {report.error}")
        raise typer.Exit(code=1)

    table = Table(title=f"Live drift check ({report.target})")
    table.add_column("Resource", style="cyan")
    table.add_column("Parameter")
    table.add_column("Expected")
    table.add_column("Live")
    table.add_column("Status")

    for name in report.in_sync:
        table.add_row(name, "-", "-", "-", "[green]In-Sync[/green]")
    for item in report.items:
        table.add_row(
            item.resource,
            item.parameter,
            item.expected,
            item.live,
            f"[red]Drifted ({item.status})[/red]",
        )
    console.print(table)

    if report.has_drift:
        for line in report.render_lines():
            console.print(line)
        console.print(
            "\n[yellow]Hint: run `infra up <file>` to re-apply the declared "
            "state, or update the .infra file to match the live state.[/yellow]"
        )
        raise typer.Exit(code=1)

    console.print("[green]No live drift detected. Cluster matches the spec.[/green]")
    raise typer.Exit(code=0)


def _check_live_drift_json(
    infra_path: Path, target: str, namespace: str
) -> Dict[str, Any]:
    """Run the live drift check, return a JSON-serializable report."""
    from infra.analyzer.drift import detect_live_drift

    report = detect_live_drift(infra_path, target=target, namespace=namespace)
    return report.to_dict()


def _doctor_all(json_output: bool) -> None:
    """Diagnose the environment AND every .infra file in the workspace."""
    from infra.cli import batch as _batch
    from infra.parser import _parser

    root = Path.cwd()
    parser = _parser()
    rows: List[_batch.BatchRow] = []
    for f in _batch.discover_infra_files(root):
        rel = _batch.display_path(f, root)
        try:
            program = parser.parse_file(f)
        except Exception as exc:
            detail = str(exc).splitlines()[0] if str(exc) else "parse error"
            rows.append(_batch.BatchRow(rel, ok=False, errors=1, detail=detail))
            continue
        from infra.analyzer.validator import SemanticValidator

        result = SemanticValidator().validate(program)
        rows.append(
            _batch.BatchRow(
                rel,
                ok=result.is_valid,
                errors=len(result.errors),
                warnings=len(result.warnings),
                detail=result.errors[0].message if result.errors else "",
            )
        )

    if json_output:
        import json as _json

        payload = _checks_json()
        payload["workspace"] = _batch.batch_payload("doctor", rows)
        typer.echo(_json.dumps(payload, indent=2))
    else:
        checks = _checks()
        typer.echo(f"Infra Lang v{__version__}")
        for c in checks:
            marker = "[OK]" if c.ok else "[FAIL]"
            typer.echo(f"{c.name}: {c.detail} {marker}")
        typer.echo("")
        _batch.emit_batch(
            "doctor", rows, title="infra doctor --all", verb="Diagnosed"
        )
    if _batch.any_failed(rows):
        raise typer.Exit(code=1)


def _checks_json() -> Dict[str, Any]:
    """Return environment checks as a JSON-friendly mapping."""
    from infra.version import __version__

    out: Dict[str, Any] = {"version": __version__}
    for c in _checks():
        out[c.name.lower().replace(" ", "_")] = {"installed": c.ok, "detail": c.detail}
    return out


def _check_drift_json(infra_path: Path, out_dir: Path, target: str) -> Dict[str, Any]:
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


def _parse_only_codes(raw: Optional[str]) -> Optional[List[str]]:
    """Validate a ``--only SEC001,REL003`` selector against known rules."""
    from infra.analyzer.autofix import FIXABLE_CODES

    if raw is None:
        return None
    codes = [c.strip().upper() for c in raw.split(",") if c.strip()]
    unknown = [c for c in codes if c not in FIXABLE_CODES]
    if unknown:
        from rich.console import Console

        Console().print(
            f"[red]Unknown autofix code(s): {', '.join(unknown)}.[/red] "
            f"Fixable: {', '.join(FIXABLE_CODES)}"
        )
        raise typer.Exit(code=1)
    if not codes:
        from rich.console import Console

        Console().print("[red]--only requires at least one code.[/red]")
        raise typer.Exit(code=1)
    return codes


def _color_diff_line(line: str) -> str:
    """Rich-markup wrapped diff line (green additions / red removals)."""
    from rich.markup import escape

    if line.startswith("+++") or line.startswith("---"):
        color = "green" if line.startswith("+") else "red"
        return f"[bold {color}]{escape(line)}[/bold {color}]"
    if line.startswith("+"):
        return f"[green]{escape(line)}[/green]"
    if line.startswith("-"):
        return f"[red]{escape(line)}[/red]"
    if line.startswith("@@"):
        return f"[cyan]{escape(line)}[/cyan]"
    return escape(line)


def _fix_mode(
    file: Path,
    *,
    apply: bool,
    dry_run: bool,
    only: Optional[List[str]],
    no_backup: bool,
    default_memory: str,
) -> None:
    """Auto-fix ``file`` in place (--fix) or preview the diff (--dry-run)."""
    from rich.console import Console

    from infra.analyzer.autofix import parse_memory_value
    from infra.analyzer.source_editor import compute_fixes, render_diff
    from infra.parser import parse_file

    console = Console()

    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)

    try:
        parse_memory_value(default_memory)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        program = parse_file(file)
    except Exception as exc:
        detail = str(exc).splitlines()[0] if str(exc) else "parse error"
        console.print(f"[red]Cannot parse {file}:[/red] {detail}")
        raise typer.Exit(code=1) from exc

    result, old_source, new_source = compute_fixes(
        program, only=only, default_memory=default_memory
    )

    if not result.changed:
        console.print("[green]No auto-fixable findings.[/green] "
                      f"{file} is already clean.")
        for skip in result.skipped:
            console.print(f"  - {skip.code} {skip.target}: {skip.description}")
        return

    if dry_run or not apply:
        diff = render_diff(
            old_source,
            new_source,
            from_name=f"{file} (current)",
            to_name=f"{file} (fixed)",
        )
        for line in diff.splitlines():
            console.print(_color_diff_line(line))
        console.print(
            f"\n[bold]{len(result.applied)} fix(es) available.[/bold] "
            f"Run `infra doctor {file} --fix` to apply "
            "(a .bak backup is created first)."
        )
        return

    if not no_backup:
        backup = file.with_suffix(file.suffix + ".bak")
        backup.write_text(
            file.read_text(encoding="utf-8"), encoding="utf-8"
        )
        console.print(f"[blue]Backup saved to[/blue] {backup}")
    file.write_text(new_source, encoding="utf-8")
    console.print(f"[green]Applied {len(result.applied)} fix(es) to[/green] {file}:")
    for fix in result.applied:
        console.print(f"  - {fix.code} {fix.target}: {fix.description}")
    for skip in result.skipped:
        console.print(f"  [yellow]~ {skip.code} {skip.target}: "
                      f"{skip.description}[/yellow]")


def doctor(
    file: Optional[Path] = typer.Argument(
        None,
        help=".infra file to auto-fix (requires --fix or --dry-run).",
    ),
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
            "(kubernetes, compose, terraform, github, helm); "
            "with --live: k8s or compose."
        ),
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help=(
            "With --check-drift: compare the spec against the LIVE state "
            "(kubectl get / docker compose ps, read-only) instead of "
            "on-disk generated files."
        ),
    ),
    namespace: str = typer.Option(
        "default",
        "--namespace",
        "-n",
        help="Kubernetes namespace for the live drift check (--live).",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit structured JSON for CI pipelines."
    ),
    all_files: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Recursively diagnose every .infra file in the workspace.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Apply auto-fixes in place (backup to .bak by default).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview the auto-fix diff without writing any changes.",
    ),
    only: Optional[str] = typer.Option(
        None,
        "--only",
        help="Fix only these codes, comma-separated "
        "(e.g. --only SEC001,REL003).",
    ),
    no_backup: bool = typer.Option(
        False,
        "--no-backup",
        help="Skip the .bak backup when using --fix.",
    ),
    default_memory: str = typer.Option(
        "512Mi",
        "--default-memory",
        help="Memory limit injected by the REL003 fix (e.g. 256Mi, 1Gi).",
    ),
) -> None:
    """Check the user's environment, detect drift, or auto-fix a file.

    Without --live the drift check compares the compiled output against
    on-disk generated files; with --live it compares the spec against the
    live cluster (k8s) or Docker Compose state (read-only probes).

    With ``--fix`` / ``--dry-run`` the command instead rewrites a single
    .infra file using the auto-fix engine (SEC001, SEC003, REL003, REL004,
    REL006, REL009).
    """
    import json as _json

    fix_mode = fix or dry_run or only is not None or no_backup
    if fix_mode:
        if file is None:
            from rich.console import Console

            Console().print(
                "[red]--fix/--dry-run require a .infra file argument.[/red] "
                "Usage: infra doctor <file.infra> --fix|--dry-run"
            )
            raise typer.Exit(code=1)
        if check_drift is not None:
            from rich.console import Console

            Console().print(
                "[red]--fix cannot be combined with --check-drift.[/red]"
            )
            raise typer.Exit(code=1)
        codes = _parse_only_codes(only)
        _fix_mode(
            file,
            apply=fix,
            dry_run=dry_run,
            only=codes,
            no_backup=no_backup,
            default_memory=default_memory,
        )
        return
    if file is not None:
        from rich.console import Console

        Console().print(
            "[red]Passing a file requires --fix or --dry-run.[/red] "
            "Without them, `infra doctor` only checks the environment."
        )
        raise typer.Exit(code=1)

    if all_files:
        _doctor_all(json_output)
        return

    if check_drift is not None and live:
        if json_output:
            try:
                payload = _check_live_drift_json(check_drift, target, namespace)
            except Exception as exc:
                payload = {
                    "target": target,
                    "has_drift": True,
                    "in_sync": [],
                    "drift": [],
                    "error": str(exc),
                }
            typer.echo(_json.dumps(payload, indent=2))
            failed = payload.get("has_drift") or payload.get("error")
            raise typer.Exit(code=1 if failed else 0)
        _check_live_drift(check_drift, target, namespace)

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
        marker = "[OK]" if c.ok else "[FAIL]"
        typer.echo(f"{c.name}: {c.detail} {marker}")

    missing = [c.name for c in checks if not c.ok]
    if missing:
        typer.echo("")
        typer.echo("Missing: " + ", ".join(missing))
        typer.echo("Some commands (compile) work without these; live Kubernetes")
        typer.echo("E2E and backend tooling need them.")
