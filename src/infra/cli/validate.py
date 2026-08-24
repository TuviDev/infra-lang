"""`infra validate` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

import typer

from infra.analyzer.environments import (
    EnvironmentNotFoundError,
    apply_environment_overlay,
)
from infra.analyzer.validator import SemanticValidator
from infra.parser import _parser


def _error_dict(e: Any, file: str = "?") -> dict[str, Any]:
    """Normalize a ValidationError or a raw parse exception into a dict."""
    loc = getattr(e, "location", None)
    return {
        "code": getattr(e, "code", "PARSE"),
        "message": getattr(e, "message", str(e)),
        "hint": getattr(e, "hint", None),
        "file": (loc.file if loc else None) or file,
        "line": loc.line if loc else None,
        "column": loc.column if loc else None,
    }


def validate(
    files: List[Path] = typer.Argument(..., help=".infra file(s) to validate"),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
    format: str = typer.Option(
        "text", "--format", help="Output format: text, json, github"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit structured JSON for CI pipelines."
    ),
    var: List[str] = typer.Option([], "--var", help="Variable: --var key=value"),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
    max_cost: Optional[float] = typer.Option(
        None,
        "--max-cost",
        help="FinOps guardrail: fail with a COST_EXCEEDED error when the "
        "estimated monthly cost exceeds this budget (in USD).",
    ),
) -> None:
    """Validate .infra files semantically (no compilation)."""
    parser = _parser()
    all_errors: list[dict[str, Any]] = []
    all_warnings = []
    any_invalid = False
    expanded: list[Path] = []
    for f in files:
        if f.is_dir():
            expanded.extend(sorted(f.rglob("*.infra")))
        else:
            expanded.append(f)

    for f in expanded:
        try:
            program = parser.parse_file(f)
            if environment:
                program = apply_environment_overlay(program, environment)
        except EnvironmentNotFoundError as exc:
            all_errors.append(
                {"code": "ENV", "message": str(exc), "hint": None, "file": str(f)}
            )
            any_invalid = True
            continue
        except Exception as exc:
            all_errors.append(_error_dict(exc, file=str(f)))
            any_invalid = True
            continue
        result = SemanticValidator().validate(program, max_cost=max_cost)
        all_errors.extend(_error_dict(e, file=str(f)) for e in result.errors)
        all_warnings.extend(result.warnings)
        if not result.is_valid or (strict and result.has_warnings):
            any_invalid = True

    if json_output:
        payload = {
            "valid": not any_invalid,
            "file": str(expanded[0]) if expanded else "",
            "errors": [
                {
                    "code": e.get("code"),
                    "message": e.get("message"),
                    "line": e.get("line"),
                    "column": e.get("column"),
                    "severity": "error",
                    "hint": e.get("hint"),
                }
                for e in all_errors
            ],
            "warnings": [
                {
                    "code": w.code,
                    "message": w.message,
                    "line": w.location.line if w.location else None,
                    "column": w.location.column if w.location else None,
                    "severity": "warning",
                    "hint": w.hint,
                }
                for w in all_warnings
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
    elif format == "json":
        payload = {
            "valid": not any_invalid,
            "errors": all_errors,
            "warnings": [w.to_dict() for w in all_warnings],
        }
        typer.echo(json.dumps(payload, indent=2))
    elif format == "github":
        for e in all_errors:
            file = e.get("file") or "?"
            line = e.get("line") or 1
            col = e.get("column") or 1
            typer.echo(f"::error file={file},line={line},col={col}::{e['message']}")
        for w in all_warnings:
            loc = w.location
            line = loc.line if loc else 1
            col = loc.column if loc else 1
            file = loc.file if loc else "?"
            typer.echo(f"::warning file={file},line={line},col={col}::{w.message}")
    else:
        if all_errors or (strict and all_warnings):
            for e in all_errors:
                file = e.get("file") or "?"
                line = e.get("line")
                col = e.get("column")
                pos = f"{file}:{line}:{col}" if line else file
                typer.echo(f"error[{e['code']}] {pos}: {e['message']}")
                if e.get("hint"):
                    typer.echo(f"  Hint: {e['hint']}")
            for w in all_warnings:
                if strict:
                    loc = w.location
                    pos = f"{loc.file}:{loc.line}:{loc.column}" if loc else "?"
                    typer.echo(f"warning[{w.code}] {pos}: {w.message}")
            typer.echo(
                f"Found {len(all_errors)} errors and {len(all_warnings)} warnings"
            )
        elif all_warnings:
            typer.echo(f"Found {len(all_warnings)} warnings")
        else:
            typer.echo("[OK] No errors found")

    if any_invalid:
        raise typer.Exit(code=1)
