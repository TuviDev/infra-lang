"""`infra ci-comment` — PR-ready report: changes, cost delta, security gates.

Generates a Markdown comment (ready for ``gh pr comment --body-file -`` or
``actions/github-script``), JSON, or plain text summarising what a ``.infra``
change does: added/removed/changed resources, the monthly cost delta and the
security (SEC*) / reliability (REL*) findings of the changed file.

Exit codes:

* ``0`` — report generated, all configured gates passed;
* ``1`` — a gate failed (--max-monthly-cost exceeded, or SEC* errors with
  ``--fail-on-security``) or the file could not be parsed;
* ``2`` — usage errors (handled by Typer).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from infra.analyzer.cost import estimate_cost
from infra.analyzer.reliability import ReliabilityChecker
from infra.analyzer.security import SecurityChecker
from infra.diff.engine import InfraDiff
from infra.errors.exceptions import ValidationError
from infra.parser import ast_nodes as n
from infra.parser import parse_file

#: Valid values for the ``--format`` option.
_FORMATS = ("github-comment", "json", "text")

#: HTML marker embedded in the Markdown comment so a bot can find and update
#: its own previous comment instead of spamming a new one on every push.
COMMENT_MARKER = "<!-- infra-lang:ci-comment -->"


def _finding_dict(finding: Any) -> Dict[str, Any]:
    """Normalise a ValidationError/Warning/ReliabilityFinding to a dict."""
    loc = getattr(finding, "location", None)
    return {
        "code": getattr(finding, "code", "?"),
        "message": getattr(finding, "message", str(finding)),
        "hint": getattr(finding, "hint", None),
        "severity": "error" if isinstance(finding, ValidationError) else "warning",
        "file": loc.file if loc else None,
        "line": loc.line if loc else None,
    }


@dataclass
class CiReport:
    """All data rendered by `infra ci-comment` (format-agnostic)."""

    source: str
    base: Optional[str]
    monthly_usd: float
    base_monthly_usd: Optional[float]
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)
    security: List[Dict[str, Any]] = field(default_factory=list)
    reliability: List[Dict[str, Any]] = field(default_factory=list)
    max_monthly_cost: Optional[float] = None
    fail_on_security: bool = False

    @property
    def delta_usd(self) -> Optional[float]:
        """Monthly cost delta vs the base file (``None`` without a base)."""
        if self.base_monthly_usd is None:
            return None
        return round(self.monthly_usd - self.base_monthly_usd, 2)

    @property
    def cost_exceeded(self) -> bool:
        """True when a --max-monthly-cost gate is set and exceeded."""
        return (
            self.max_monthly_cost is not None
            and self.monthly_usd > self.max_monthly_cost
        )

    @property
    def security_errors(self) -> List[Dict[str, Any]]:
        """Security findings with severity ``error`` (gate-relevant)."""
        return [f for f in self.security if f["severity"] == "error"]

    @property
    def security_failed(self) -> bool:
        """True when --fail-on-security is on and SEC* errors exist."""
        return self.fail_on_security and bool(self.security_errors)

    @property
    def gate_passed(self) -> bool:
        return not self.cost_exceeded and not self.security_failed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "base": self.base,
            "cost": {
                "monthly_usd": self.monthly_usd,
                "base_monthly_usd": self.base_monthly_usd,
                "delta_usd": self.delta_usd,
            },
            "changes": {
                "added": self.added,
                "removed": self.removed,
                "changed": self.changed,
            },
            "security": self.security,
            "reliability": self.reliability,
            "gates": {
                "max_monthly_cost": self.max_monthly_cost,
                "cost_exceeded": self.cost_exceeded,
                "fail_on_security": self.fail_on_security,
                "security_failed": self.security_failed,
                "passed": self.gate_passed,
            },
        }


def build_report(
    program: n.Program,
    *,
    source: str,
    base_program: Optional[n.Program] = None,
    base_source: Optional[str] = None,
    max_monthly_cost: Optional[float] = None,
    fail_on_security: bool = False,
) -> CiReport:
    """Compute the cost/diff/security data for the head *program*.

    With *base_program* given, resources are diffed base→head and the monthly
    cost delta is included in the report.
    """
    monthly = estimate_cost(program).total_monthly_usd
    base_monthly: Optional[float] = None
    added: List[str] = []
    removed: List[str] = []
    changed: List[str] = []

    if base_program is not None:
        base_monthly = estimate_cost(base_program).total_monthly_usd
        diff = InfraDiff().diff(base_program, program)
        added = [f"{item.kind} `{item.name}`" for item in diff.added]
        removed = [f"{item.kind} `{item.name}`" for item in diff.removed]
        changed = [
            f"{item.kind} `{item.name}` ({len(item.changes)} change(s))"
            for item in diff.changed
        ]

    security = [_finding_dict(f) for f in SecurityChecker().check(program)]
    reliability = [_finding_dict(f) for f in ReliabilityChecker().check(program)]

    return CiReport(
        source=source,
        base=base_source,
        monthly_usd=monthly,
        base_monthly_usd=base_monthly,
        added=added,
        removed=removed,
        changed=changed,
        security=security,
        reliability=reliability,
        max_monthly_cost=max_monthly_cost,
        fail_on_security=fail_on_security,
    )


def _delta_label(report: CiReport) -> str:
    delta = report.delta_usd
    if delta is None:
        return "—"
    if delta > 0:
        return f"🔺 +${delta:.2f}"
    if delta < 0:
        return f"🟢 −${abs(delta):.2f}"
    return "±$0.00"


def _gate_lines_markdown(report: CiReport) -> List[str]:
    lines: List[str] = []
    if report.max_monthly_cost is not None:
        if report.cost_exceeded:
            over = round(report.monthly_usd - report.max_monthly_cost, 2)
            lines.append(
                f"- ❌ **cost gate:** ${report.monthly_usd:.2f}/mo exceeds the "
                f"${report.max_monthly_cost:.2f} limit by ${over:.2f}"
            )
        else:
            lines.append(
                f"- ✅ **cost gate:** ${report.monthly_usd:.2f}/mo within the "
                f"${report.max_monthly_cost:.2f} limit"
            )
    if report.fail_on_security:
        if report.security_failed:
            count = len(report.security_errors)
            lines.append(
                f"- ❌ **security gate:** {count} SEC* error finding(s) "
                "(--fail-on-security)"
            )
        else:
            lines.append(
                "- ✅ **security gate:** no SEC* error findings "
                "(--fail-on-security)"
            )
    return lines


def render_markdown(report: CiReport) -> str:
    """Render the report as a GitHub-flavoured Markdown PR comment."""
    lines: List[str] = [
        COMMENT_MARKER,
        "## 🚀 Infra Lang — PR report",
        "",
        f"**File:** `{report.source}`"
        + (f"  ·  **base:** `{report.base}`" if report.base else ""),
        "",
        "### 💰 Monthly cost",
        "",
    ]
    if report.base_monthly_usd is not None:
        lines += [
            "| Base | This PR | Delta |",
            "| ---: | ---: | ---: |",
            f"| ${report.base_monthly_usd:.2f} | ${report.monthly_usd:.2f} "
            f"| {_delta_label(report)} |",
            "",
        ]
    else:
        lines += [f"**Total:** ${report.monthly_usd:.2f} / month", ""]

    if report.added or report.removed or report.changed:
        lines += ["### 📦 Changes", ""]
        lines += [f"- ➕ {item}" for item in report.added]
        lines += [f"- ➖ {item}" for item in report.removed]
        lines += [f"- ✏️ {item}" for item in report.changed]
        lines.append("")
    elif report.base is not None:
        lines += ["### 📦 Changes", "", "- No resource changes.", ""]

    if report.security:
        lines += ["### 🔒 Security findings", ""]
        for f in report.security:
            icon = "❌" if f["severity"] == "error" else "⚠️"
            lines.append(f"- {icon} `{f['code']}` — {f['message']}")
        lines.append("")

    if report.reliability:
        lines += ["### 🛡️ Reliability hints", ""]
        for f in report.reliability:
            lines.append(f"- ⚠️ `{f['code']}` — {f['message']}")
        lines.append("")

    gate_lines = _gate_lines_markdown(report)
    if gate_lines:
        lines += ["### 🚦 Gates", ""] + gate_lines + [""]

    lines += [
        "---",
        "_Generated by [infra-lang](https://github.com/TuviDev/infra-lang) "
        "`infra ci-comment`_",
    ]
    return "\n".join(lines)


def render_json(report: CiReport) -> str:
    """Render the report as JSON for machine consumers."""
    return json.dumps(report.to_dict(), indent=2)


def render_text(report: CiReport) -> str:
    """Render the report as plain ASCII text (terminals, logs, email)."""
    lines: List[str] = [
        f"infra ci-comment: {report.source}"
        + (f" (base: {report.base})" if report.base else ""),
        f"monthly cost: ${report.monthly_usd:.2f}",
    ]
    delta = report.delta_usd
    if delta is not None:
        lines.append(f"cost delta: {'+' if delta >= 0 else ''}{delta:.2f} USD/mo")
    for label, items in (
        ("added", report.added),
        ("removed", report.removed),
        ("changed", report.changed),
    ):
        for item in items:
            lines.append(f"{label}: {item}")
    for f in report.security:
        lines.append(f"{f['severity']}[{f['code']}]: {f['message']}")
    for f in report.reliability:
        lines.append(f"warning[{f['code']}]: {f['message']}")
    if report.max_monthly_cost is not None:
        state = "EXCEEDED" if report.cost_exceeded else "ok"
        lines.append(
            f"cost gate: ${report.monthly_usd:.2f} / "
            f"limit ${report.max_monthly_cost:.2f} -> {state}"
        )
    if report.fail_on_security:
        state = "FAILED" if report.security_failed else "ok"
        lines.append(f"security gate: {state}")
    lines.append(f"gate: {'PASSED' if report.gate_passed else 'FAILED'}")
    return "\n".join(lines)


def _fail_hint(report: CiReport) -> str:
    reasons: List[str] = []
    if report.cost_exceeded:
        reasons.append("monthly cost limit exceeded")
    if report.security_failed:
        reasons.append("SEC* error findings present (--fail-on-security)")
    return "; ".join(reasons)


def ci_comment_cmd(
    file: Path = typer.Argument(..., help=".infra file changed by the PR"),
    base: Optional[Path] = typer.Option(
        None,
        "--base",
        "-b",
        help="Baseline .infra file (e.g. checked out from the base branch) "
        "to diff against.",
    ),
    output_format: str = typer.Option(
        "github-comment",
        "--format",
        "-f",
        help="Output format: github-comment | json | text",
    ),
    max_monthly_cost: Optional[float] = typer.Option(
        None,
        "--max-monthly-cost",
        help="Fail (exit 1) when the estimated monthly cost exceeds this "
        "USD amount.",
    ),
    fail_on_security: bool = typer.Option(
        False,
        "--fail-on-security",
        help="Fail (exit 1) when SEC* error findings are present.",
    ),
    environment: Optional[str] = typer.Option(
        None, "--environment", "-e", "--env", help="Environment overlay name"
    ),
) -> None:
    """Generate a PR-ready CI comment (changes, cost delta, SEC*/REL*)."""
    from rich.console import Console

    console = Console(stderr=True)

    fmt = output_format.lower()
    if fmt not in _FORMATS:
        console.print(
            f"[red]Unknown format '{output_format}'.[/red] "
            "Valid formats: github-comment, json, text"
        )
        raise typer.Exit(code=1)

    if not file.exists():
        console.print(f"[red]Source file not found:[/red] {file}")
        raise typer.Exit(code=1)
    if base is not None and not base.exists():
        console.print(f"[red]Base file not found:[/red] {base}")
        raise typer.Exit(code=1)

    from infra.cli.compile import _apply_environment

    try:
        program = _apply_environment(
            parse_file(file), environment or ""
        )
        base_program = (
            _apply_environment(parse_file(base), environment or "")
            if base is not None
            else None
        )
    except typer.Exit:
        raise
    except Exception as exc:  # parse errors et al.
        console.print(f"[red]ci-comment failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    report = build_report(
        program,
        source=str(file),
        base_program=base_program,
        base_source=str(base) if base is not None else None,
        max_monthly_cost=max_monthly_cost,
        fail_on_security=fail_on_security,
    )

    if fmt == "json":
        typer.echo(render_json(report))
    elif fmt == "text":
        typer.echo(render_text(report))
    else:
        typer.echo(render_markdown(report))

    if report.gate_passed:
        console.print("[OK] ci-comment gates passed.")
    else:
        console.print(f"[FAIL] {_fail_hint(report)}")
        raise typer.Exit(code=1)
