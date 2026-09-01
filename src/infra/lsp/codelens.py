"""CodeLens FinOps badges for the Infra language server (v0.9.0).

Computes per-block insight lenses (cost, replicas, warnings, grades) above
``service`` / ``database`` / ``environment`` / ``queue`` / ``cache`` /
``storage`` declarations. Pure computation — the only LSP-typed objects
built here are plain :class:`lsprotocol.types.CodeLens` values, so the
module works identically on pygls 2.x (dev) and pygls 1.3.1 (legacy).

Emoji labels have an ASCII-safe fallback (``[$]``, ``[R]``, ``[!]``,
``[G:x]``, ``[DB]``, ``[ENV]`` …) for terminals/editors without Unicode
support; it can be forced or disabled with the ``infra.codelens.emoji``
setting (``auto`` follows the locale).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Tuple

from lsprotocol.types import (
    CodeLens,
    Command,
    Position,
    Range,
)

from infra.analyzer.cost import estimate_cost
from infra.analyzer.reliability import ReliabilityChecker
from infra.analyzer.security import SecurityChecker
from infra.explain import _grade
from infra.parser import ast_nodes as n
from infra.parser import parse as _parse

#: Emoji prefixes (Unicode editors).
_E = {
    "cost": "💰",
    "replicas": "⚡",
    "warnings": "🔒",
    "grade": "📊",
    "db": "💾",
    "env": "🌍",
    "target": "🎯",
    "queue": "📨",
    "cache": "⚡",
    "storage": "📦",
}
#: ASCII-safe fallbacks keyed the same way. ``{v}`` placeholders are filled
#: with the rendered value (grade letter / count).
_A = {
    "cost": "[$]",
    "replicas": "[R]",
    "warnings": "[!]",
    "grade": "[G:{v}]",
    "db": "[DB]",
    "env": "[ENV]",
    "target": "[T]",
    "queue": "[Q]",
    "cache": "[C]",
    "storage": "[ST]",
}


@dataclass(frozen=True)
class LensOptions:
    """Feature flags controlling which badges are computed."""

    enabled: bool = True
    show_cost: bool = True
    show_security: bool = True
    show_reliability: bool = True
    emoji: bool = True


def resolve_emoji(setting: str, environ: Mapping[str, str]) -> bool:
    """Resolve the ``infra.codelens.emoji`` setting.

    ``auto`` follows the locale (``LANG`` / ``LC_ALL`` mentioning UTF);
    anything other than ``true``/``false``/``auto`` also falls back to the
    locale check so a misconfiguration can never crash the handler.
    """
    normalized = setting.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    locale = " ".join(
        environ.get(k, "") for k in ("LC_ALL", "LC_CTYPE", "LANG")
    ).upper()
    return "UTF" in locale


def options_from_initialization(
    init_options: Any, environ: Optional[Mapping[str, str]] = None
) -> LensOptions:
    """Build :class:`LensOptions` from the client's initialization options.

    Expected shape (dot-flattened keys, as sent by the VS Code extension)::

        {
          "infra.codelens.enabled": true,
          "infra.codelens.showCost": true,
          "infra.codelens.showSecurity": true,
          "infra.codelens.showReliability": true,
          "infra.codelens.emoji": "auto",
        }

    Unknown / missing values fall back to the defaults; ``None`` or a
    non-dict value yields the all-on default configuration.
    """
    env = os.environ if environ is None else environ
    if not isinstance(init_options, dict):
        return LensOptions(emoji=resolve_emoji("auto", env))
    get = init_options.get

    def flag(key: str, default: bool) -> bool:
        value = get(key, default)
        return value if isinstance(value, bool) else default

    emoji_setting = get("infra.codelens.emoji", "auto")
    if not isinstance(emoji_setting, str):
        emoji_setting = "auto"
    return LensOptions(
        enabled=flag("infra.codelens.enabled", True),
        show_cost=flag("infra.codelens.showCost", True),
        show_security=flag("infra.codelens.showSecurity", True),
        show_reliability=flag("infra.codelens.showReliability", True),
        emoji=resolve_emoji(emoji_setting, env),
    )


def _budget(parts: List[str], opts: LensOptions) -> str:
    # ASCII-safe mode also swaps the middle-dot separator for a plain bar.
    sep = " · " if opts.emoji else " | "
    return sep.join(parts)


def _pos(stmt: Any) -> Position:
    line = getattr(getattr(stmt, "location", None), "line", 1) or 1
    return Position(line=max(0, line - 1), character=0)


def _lens(stmt: Any, title: str) -> CodeLens:
    pos = _pos(stmt)
    return CodeLens(
        range=Range(start=pos, end=pos),
        command=Command(title=title, command=""),
    )


def _tag(opts: LensOptions, key: str, value: Any = "") -> str:
    if opts.emoji:
        return _E[key]
    return _A[key].format(v=value)


def _service_lens(
    svc: n.ServiceDef,
    opts: LensOptions,
    cost_by_name: Mapping[str, float],
    sec_count: int,
    rel_grade: str,
) -> CodeLens:
    parts: List[str] = []
    if opts.show_cost:
        parts.append(f"{_tag(opts, 'cost')} ${cost_by_name.get(svc.name, 0.0):.2f}/mo")
    parts.append(f"{_tag(opts, 'replicas')} {svc.replicas} replicas")
    if opts.show_security:
        w = "warning" if sec_count == 1 else "warnings"
        parts.append(f"{_tag(opts, 'warnings', sec_count)} {sec_count} {w}")
    if opts.show_reliability:
        parts.append(f"{_tag(opts, 'grade', rel_grade)} Grade: {rel_grade}")
    return _lens(svc, _budget(parts, opts))


def _database_lens(
    db: n.DatabaseDef, opts: LensOptions, cost_by_name: Mapping[str, float]
) -> CodeLens:
    parts: List[str] = []
    size = _format_resource(db.storage or db.size) or "100Gi"
    parts.append(f"{_tag(opts, 'db')} {size}")
    if opts.show_cost:
        parts.append(f"{_tag(opts, 'cost')} ${cost_by_name.get(db.name, 0.0):.2f}/mo")
    if opts.show_reliability:
        state = (
            "enabled"
            if (db.backup is not None and db.backup.enabled)
            else "disabled"
        )
        parts.append(f"{_tag(opts, 'warnings')} Backup: {state}")
    return _lens(db, _budget(parts, opts))


def _environment_lens(
    env: n.EnvironmentDef,
    opts: LensOptions,
    service_count: int,
    total_usd: float,
) -> CodeLens:
    parts = [
        f"{_tag(opts, 'env')} {service_count} "
        f"{'service' if service_count == 1 else 'services'}"
    ]
    if opts.show_cost:
        parts.append(f"{_tag(opts, 'cost')} ${total_usd:.2f}/mo total")
    parts.append(f"{_tag(opts, 'target')} Target: {env.provider or 'kubernetes'}")
    return _lens(env, _budget(parts, opts))


def _queue_lens(queue: n.QueueDef, opts: LensOptions) -> CodeLens:
    parts = [f"{_tag(opts, 'queue')} {queue.type}"]
    parts.append(f"{queue.replicas} replicas")
    topics = len(queue.topics)
    if topics:
        parts.append(f"{topics} {'topic' if topics == 1 else 'topics'}")
    return _lens(queue, _budget(parts, opts))


def _cache_lens(
    cache: n.CacheDef, opts: LensOptions, cost_by_name: Mapping[str, float]
) -> CodeLens:
    parts = [f"{_tag(opts, 'cache')} {cache.type}"]
    if opts.show_cost:
        parts.append(
            f"{_tag(opts, 'cost')} ${cost_by_name.get(cache.name, 0.0):.2f}/mo"
        )
    if opts.show_reliability:
        parts.append(f"persistence: {'on' if cache.persistence else 'off'}")
    return _lens(cache, _budget(parts, opts))


def _storage_lens(
    storage: n.StorageDef, opts: LensOptions, cost_by_name: Mapping[str, float]
) -> CodeLens:
    parts = [f"{_tag(opts, 'storage')} {_format_resource(storage.size) or '100Gi'}"]
    if opts.show_cost:
        parts.append(
            f"{_tag(opts, 'cost')} ${cost_by_name.get(storage.name, 0.0):.2f}/mo"
        )
    parts.append(storage.type)
    return _lens(storage, _budget(parts, opts))


def _format_resource(rv: Optional[n.ResourceValue]) -> Optional[str]:
    """Render a ResourceValue like ``20Gi`` (unit-less int when no unit)."""
    if rv is None:
        return None
    value = float(rv.value)
    num = str(int(value)) if value.is_integer() else str(value)
    return f"{num}{rv.unit}" if rv.unit else num


def build_lenses(source: str, opts: LensOptions) -> List[CodeLens]:
    """Compute all CodeLens values for *source*.

    Never raises on malformed input: a parse failure yields an empty list
    (the editor simply shows no badges until the file parses again).
    """
    if not opts.enabled:
        return []
    try:
        program = _parse(source)
    except Exception:
        return []

    est = estimate_cost(program)
    cost_by_name = {item.name: item.monthly_usd for item in est.items}
    total_usd = est.total_monthly_usd
    service_count = sum(
        1 for s in program.statements if isinstance(s, n.ServiceDef)
    )

    lenses: List[CodeLens] = []
    for stmt in program.statements:
        if isinstance(stmt, n.ServiceDef):
            solo = n.Program(statements=(stmt,))
            sec_count = len(SecurityChecker().check(solo))
            rel_grade = _grade(len(ReliabilityChecker().check(solo)))
            lenses.append(
                _service_lens(stmt, opts, cost_by_name, sec_count, rel_grade)
            )
        elif isinstance(stmt, n.DatabaseDef):
            lenses.append(_database_lens(stmt, opts, cost_by_name))
        elif isinstance(stmt, n.EnvironmentDef):
            lenses.append(
                _environment_lens(stmt, opts, service_count, total_usd)
            )
        elif isinstance(stmt, n.QueueDef):
            lenses.append(_queue_lens(stmt, opts))
        elif isinstance(stmt, n.CacheDef):
            lenses.append(_cache_lens(stmt, opts, cost_by_name))
        elif isinstance(stmt, n.StorageDef):
            lenses.append(_storage_lens(stmt, opts, cost_by_name))
    return lenses


# --------------------------------------------------------------------------- #
# Hover "Insight" card (reused by the server hover handler)
# --------------------------------------------------------------------------- #

#: Matches a top-level block declaration line: `service api {` or
#: `database "db" {` (both bare and quoted names).
_BLOCK_DECL_RE = re.compile(
    r'^\s*(service|database|cache|queue|storage)\s+"?([A-Za-z_][A-Za-z0-9_-]*)"?'
)


def block_decl_at(line_text: str) -> Optional[Tuple[str, str]]:
    """Return ``(kind, name)`` when *line_text* declares a block, else None."""
    match = _BLOCK_DECL_RE.match(line_text)
    if match is None:
        return None
    return match.group(1), match.group(2)


def hover_insight_section(source: str, kind: str, name: str) -> Optional[str]:
    """Markdown "💡 Insight" card for a named block (hover expansion).

    Returns ``None`` when the source does not parse or the block is absent,
    so callers can silently fall back to the plain keyword hover.
    """
    try:
        program = _parse(source)
    except Exception:
        return None

    target: Optional[Any] = None
    for stmt in program.statements:
        stmt_name = getattr(stmt, "name", None)
        if stmt_name == name and stmt.__class__.__name__ == kind.capitalize() + "Def":
            target = stmt
            break
    if target is None:
        return None

    est = estimate_cost(program)
    item = next((i for i in est.items if i.name == name), None)

    findings_sec: List[Any] = []
    findings_rel: List[Any] = []
    if isinstance(target, (n.ServiceDef, n.DatabaseDef, n.CacheDef)):
        solo = n.Program(statements=(target,))
        findings_sec = SecurityChecker().check(solo)
        findings_rel = ReliabilityChecker().check(solo)

    lines: List[str] = ["---", "", "### 💡 Insight", ""]
    lines.append(f"**{kind}** `{name}`")
    if item is not None:
        parts = []
        if item.vcpu:
            parts.append(f"compute {item.vcpu:.2f} vCPU")
        if item.ram_gb:
            parts.append(f"RAM {item.ram_gb:.2f} GB")
        if item.storage_gb:
            parts.append(f"storage {item.storage_gb:.2f} GB")
        detail = "; ".join(parts) if parts else "flat rate"
        lines.append(
            f"**Cost:** ${item.monthly_usd:.2f}/mo ({detail})"
        )
    else:
        lines.append("**Cost:** not metered by the static estimator")
    if isinstance(target, n.ServiceDef):
        deps = list(target.dependencies)
        dependents = [
            s.name
            for s in program.statements
            if isinstance(s, n.ServiceDef) and name in s.dependencies
        ]
        lines.append(
            "**Depends on:** " + (", ".join(deps) if deps else "nothing")
        )
        lines.append(
            "**Depended on by:** "
            + (", ".join(dependents) if dependents else "nobody")
        )
    if findings_sec or findings_rel:
        lines.append("")
        lines.append("**Warnings:**")
        for f in findings_sec:
            lines.append(f"- `{f.code}` {f.message.splitlines()[0]}")
        for f in findings_rel:
            lines.append(f"- `{f.code}` {f.message.splitlines()[0]}")
        hints = [
            f.hint for f in (*findings_sec, *findings_rel) if getattr(f, "hint", None)
        ]
        if hints:
            lines.append("")
            lines.append("**Suggested optimizations:**")
            for hint in hints:
                lines.append(f"- {hint}")
    else:
        lines.append("**Warnings:** none")
    return "\n".join(lines)


__all__ = [
    "LensOptions",
    "block_decl_at",
    "build_lenses",
    "hover_insight_section",
    "options_from_initialization",
    "resolve_emoji",
]
