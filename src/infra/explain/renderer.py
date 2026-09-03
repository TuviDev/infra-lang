"""Renderers turning :class:`infra.explain.ExplainData` into reports (v0.9.0).

Three output formats (``markdown`` / ``text`` / ``json``) x two audiences
(``human`` / ``ai``). The ``ai`` audience is optimized for LLM context
windows: compact JSON, no decorative padding, plus a ``_meta`` envelope and
a deterministic ``_summary`` generated from fixed templates (no LLM at
runtime). Given the same input and the same ``now`` value, the output is
bit-for-bit reproducible.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from infra.errors.exceptions import InfraError
from infra.explain import SECTION_TITLES, ExplainData


class InvalidSectionsError(InfraError, ValueError):
    """Invalid ``infra explain --sections`` selection.

    Inherits :class:`ValueError` for backwards compatibility; it is also an
    :class:`InfraError`, keeping every module error in one hierarchy.
    """

#: Formats accepted by the CLI.
FORMATS = ("markdown", "text", "json")
#: Audiences accepted by the CLI.
AUDIENCES = ("human", "ai")


def _loc(item: Dict[str, Any]) -> str:
    """``file:line`` suffix for a finding, or an empty string."""
    if item.get("line") is None:
        return ""
    file = item.get("file") or "<source>"
    return f"{file}:{item['line']}"


def _pct(part: float, total: float) -> float:
    """Percentage share with 1 decimal; 0.0 when the total is zero."""
    if total <= 0:
        return 0.0
    return round(part * 100.0 / total, 1)


# --------------------------------------------------------------------------- #
# Markdown renderer
# --------------------------------------------------------------------------- #


def _md_overview(data: ExplainData, out: List[str]) -> None:
    c = data.counts
    out += [
        "## Overview",
        "",
        f"- **Project:** {data.project}",
        f"- **Architecture type:** {data.arch_type}",
        (
            f"- **Topology:** {c['services']} service(s), "
            f"{c['databases']} database(s), {c['queues']} queue(s), "
            f"{c['caches']} cache(s), {c['storages']} storage(s), "
            f"{c['pipelines']} pipeline(s)"
        ),
        f"- **Dependency edges:** {data.total_dependencies}",
        f"- **Technologies:** "
        f"{', '.join(data.tech_stack) if data.tech_stack else 'n/a'}",
    ]
    out.append("- **Top costs:**")
    if data.top_costs:
        for item in data.top_costs:
            out.append(
                f"  - `{item['name']}` ({item['kind']}) — "
                f"${item['monthly_usd']:.2f}/mo"
            )
    else:
        out.append("  - no billable resources")
    out.append("")


def _md_services(data: ExplainData, out: List[str]) -> None:
    out += [
        "## Services",
        "",
        "| Service | Image | Replicas | Port | Monthly | Health | Sec | Rel |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for s in data.services:
        out.append(
            f"| `{s.name}` | {s.image} | {s.replicas} | {s.port} "
            f"| ${s.monthly_usd:.2f} | {s.health} "
            f"| {s.security_grade} | {s.reliability_grade} |"
        )
    if not data.services:
        out.append("| *(no services defined)* | | | | | | | |")
    out.append("")


def _dep_lines(data: ExplainData) -> List[str]:
    lines: List[str] = []
    for name in sorted(data.dependencies):
        deps = data.dependencies[name]
        if deps:
            lines.append(f"`{name}` depends on: " + ", ".join(f"`{d}`" for d in deps))
        else:
            lines.append(f"`{name}` has no dependencies")
    if not lines:
        lines.append("*(no services defined)*")
    return lines


def _md_deps(data: ExplainData, out: List[str]) -> None:
    out += ["## Dependencies", ""]
    out += ["- " + line for line in _dep_lines(data)]
    out.append("")
    if data.spofs:
        out.append("**Single points of failure:**")
        for spof in data.spofs:
            out.append(
                f"- `{spof['name']}` — {spof['replicas']} replica(s), "
                f"{spof['dependents']} dependent(s)"
            )
    else:
        out.append("No single points of failure detected.")
    out.append("")


def _md_cost(data: ExplainData, out: List[str]) -> None:
    out += [
        "## Cost Breakdown",
        "",
        f"**Total: ${data.cost_total_usd:.2f}/mo** (static estimate)",
        "",
        "| Resource | Kind | Monthly | Share |",
        "| --- | --- | ---: | ---: |",
    ]
    for item in data.cost_items:
        share = _pct(item["monthly_usd"], data.cost_total_usd)
        out.append(
            f"| `{item['name']}` | {item['kind']} "
            f"| ${item['monthly_usd']:.2f} | {share:.1f}% |"
        )
    if not data.cost_items:
        out.append("| *(no billable resources)* | | | |")
    out += ["", "**By category:**", ""]
    for cat in ("compute", "storage", "network", "managed"):
        value = data.cost_categories.get(cat, 0.0)
        out.append(f"- {cat}: ${value:.2f} ({_pct(value, data.cost_total_usd):.1f}%)")
    out.append("")


def _md_security(data: ExplainData, out: List[str]) -> None:
    out += ["## Security Warnings", ""]
    if not data.security:
        out += ["No security warnings.", ""]
        return
    for f in data.security:
        loc = _loc(f)
        loc_txt = f" ({loc})" if loc else ""
        hint = f" — Fix: {f['hint']}" if f.get("hint") else ""
        out.append(f"- `{f['code']}`{loc_txt} {f['message']}{hint}")
    out.append("")


def _md_reliability(data: ExplainData, out: List[str]) -> None:
    out += ["## Reliability Report", ""]
    if not data.reliability:
        out += ["No reliability findings.", ""]
        return
    for f in data.reliability:
        loc = _loc(f)
        loc_txt = f" ({loc})" if loc else ""
        hint = f" — Fix: {f['hint']}" if f.get("hint") else ""
        out.append(
            f"- `{f['code']}` [{f['impact']} impact]{loc_txt} {f['message']}{hint}"
        )
    out.append("")


def _md_whatif(data: ExplainData, out: List[str]) -> None:
    out += ["## What-If Scenarios", "", "### Failure impact", ""]
    if data.whatif_failure:
        for w in data.whatif_failure:
            if w["affected"]:
                affected = ", ".join(f"`{a}`" for a in w["affected"])
                out.append(f"- If `{w['target']}` fails → also down: {affected}")
            else:
                out.append(f"- If `{w['target']}` fails → no other service affected")
    else:
        out.append("- *(no services to simulate)*")
    out += ["", "### Cost & reliability of scaling x2", ""]
    if data.whatif_scale:
        for w in data.whatif_scale:
            rel = w["reliability_delta"]
            rel_txt = f"+{rel}" if rel > 0 else str(rel)
            out.append(
                f"- `{w['name']}` replicas {w['current_replicas']}→"
                f"{w['new_replicas']}: cost +${w['cost_delta_usd']:.2f}/mo, "
                f"reliability score {rel_txt}"
            )
    else:
        out.append("- *(no services to simulate)*")
    out.append("")


_MD_SECTIONS = {
    "overview": _md_overview,
    "services": _md_services,
    "deps": _md_deps,
    "cost": _md_cost,
    "security": _md_security,
    "reliability": _md_reliability,
    "whatif": _md_whatif,
}


# --------------------------------------------------------------------------- #
# Plain-text renderer
# --------------------------------------------------------------------------- #


def _txt_header(title: str, out: List[str]) -> None:
    out += [f"== {title} " + "=" * max(0, 60 - len(title) - 4), ""]


def _txt_overview(data: ExplainData, out: List[str]) -> None:
    _txt_header("Overview", out)
    c = data.counts
    tech = ", ".join(data.tech_stack) if data.tech_stack else "n/a"
    out += [
        f"Project:        {data.project}",
        f"Architecture:   {data.arch_type}",
        (
            f"Topology:       {c['services']} services, {c['databases']} db, "
            f"{c['queues']} queues, {c['caches']} caches, "
            f"{c['storages']} storages, {c['pipelines']} pipelines"
        ),
        f"Dependencies:   {data.total_dependencies} edge(s)",
        f"Technologies:   {tech}",
        "Top costs:",
    ]
    if data.top_costs:
        for item in data.top_costs:
            out.append(
                f"  - {item['name']} ({item['kind']}): ${item['monthly_usd']:.2f}/mo"
            )
    else:
        out.append("  - no billable resources")
    out.append("")


def _txt_services(data: ExplainData, out: List[str]) -> None:
    _txt_header("Services", out)
    if not data.services:
        out += ["(no services defined)", ""]
        return
    rows = [
        (
            s.name,
            s.image,
            str(s.replicas),
            s.port,
            f"${s.monthly_usd:.2f}",
            s.health,
            s.security_grade,
            s.reliability_grade,
        )
        for s in data.services
    ]
    header = ("SERVICE", "IMAGE", "REPL", "PORT", "MONTHLY", "HEALTH", "SEC", "REL")
    widths = [max(len(header[i]), max(len(r[i]) for r in rows)) for i in range(8)]
    out.append("  ".join(header[i].ljust(widths[i]) for i in range(8)))
    out.append("  ".join("-" * widths[i] for i in range(8)))
    for r in rows:
        out.append("  ".join(r[i].ljust(widths[i]) for i in range(8)))
    out.append("")


def _txt_deps(data: ExplainData, out: List[str]) -> None:
    _txt_header("Dependencies", out)
    for line in _dep_lines(data):
        out.append("- " + line.replace("`", ""))
    out.append("")
    if data.spofs:
        out.append("SINGLE POINTS OF FAILURE:")
        for spof in data.spofs:
            out.append(
                f"  - {spof['name']}: {spof['replicas']} replica(s), "
                f"{spof['dependents']} dependent(s)"
            )
    else:
        out.append("No single points of failure detected.")
    out.append("")


def _txt_cost(data: ExplainData, out: List[str]) -> None:
    _txt_header("Cost Breakdown", out)
    out.append(f"TOTAL: ${data.cost_total_usd:.2f}/mo (static estimate)")
    out.append("")
    if not data.cost_items:
        out.append("  (no billable resources)")
    for item in data.cost_items:
        share = _pct(item["monthly_usd"], data.cost_total_usd)
        out.append(
            f"  {item['name']:<20} {item['kind']:<10} "
            f"${item['monthly_usd']:>8.2f}  {share:>5.1f}%"
        )
    out += ["", "By category:"]
    for cat in ("compute", "storage", "network", "managed"):
        value = data.cost_categories.get(cat, 0.0)
        out.append(
            f"  {cat:<10} ${value:>8.2f}  {_pct(value, data.cost_total_usd):>5.1f}%"
        )
    out.append("")


def _txt_whatif(data: ExplainData, out: List[str]) -> None:
    _txt_header("What-If Scenarios", out)
    out.append("Failure impact:")
    if data.whatif_failure:
        for w in data.whatif_failure:
            if w["affected"]:
                out.append(
                    f"  {w['target']} fails -> also down: {', '.join(w['affected'])}"
                )
            else:
                out.append(f"  {w['target']} fails -> no other service affected")
    else:
        out.append("  (no services to simulate)")
    out += ["", "Scaling x2 (cost / reliability deltas):"]
    if data.whatif_scale:
        for w in data.whatif_scale:
            rel = w["reliability_delta"]
            rel_txt = f"+{rel}" if rel > 0 else str(rel)
            out.append(
                f"  {w['name']}: {w['current_replicas']}->{w['new_replicas']} "
                f"replicas, +${w['cost_delta_usd']:.2f}/mo, "
                f"reliability {rel_txt}"
            )
    else:
        out.append("  (no services to simulate)")
    out.append("")


def _txt_security(data: ExplainData, out: List[str]) -> None:
    _txt_header("Security Warnings", out)
    if not data.security:
        out.append("(none)")
    for f in data.security:
        loc = _loc(f)
        loc_txt = f" [{loc}]" if loc else ""
        hint = f" | fix: {f['hint']}" if f.get("hint") else ""
        out.append(f"{f['code']}{loc_txt}: {f['message']}{hint}")
    out.append("")


def _txt_reliability(data: ExplainData, out: List[str]) -> None:
    _txt_header("Reliability Report", out)
    if not data.reliability:
        out.append("(none)")
    for f in data.reliability:
        loc = _loc(f)
        loc_txt = f" [{loc}]" if loc else ""
        hint = f" | fix: {f['hint']}" if f.get("hint") else ""
        out.append(f"{f['code']} [{f['impact']} impact]{loc_txt}: {f['message']}{hint}")
    out.append("")


_TXT_SECTIONS = {
    "overview": _txt_overview,
    "services": _txt_services,
    "deps": _txt_deps,
    "cost": _txt_cost,
    "security": _txt_security,
    "reliability": _txt_reliability,
    "whatif": _txt_whatif,
}


# --------------------------------------------------------------------------- #
# JSON payload builder
# --------------------------------------------------------------------------- #


def _json_payload(data: ExplainData, sections: Sequence[str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "project": data.project,
        "arch_type": data.arch_type,
        "tech_stack": data.tech_stack,
        "checksum": data.checksum,
    }
    if "overview" in sections:
        payload["overview"] = {
            "counts": data.counts,
            "total_dependencies": data.total_dependencies,
            "top_costs": data.top_costs,
        }
    if "services" in sections:
        payload["services"] = [
            {
                "name": s.name,
                "image": s.image,
                "replicas": s.replicas,
                "port": s.port,
                "monthly_usd": s.monthly_usd,
                "health": s.health,
                "security_grade": s.security_grade,
                "reliability_grade": s.reliability_grade,
            }
            for s in data.services
        ]
    if "deps" in sections:
        payload["dependencies"] = {
            "service_dependencies": data.dependencies,
            "dependents": data.dependents,
            "single_points_of_failure": data.spofs,
        }
    if "cost" in sections:
        payload["cost"] = {
            "total_monthly_usd": data.cost_total_usd,
            "items": data.cost_items,
            "categories": data.cost_categories,
        }
    if "security" in sections:
        payload["security"] = data.security
    if "reliability" in sections:
        payload["reliability"] = data.reliability
    if "whatif" in sections:
        payload["what_if"] = {
            "failure_impact": data.whatif_failure,
            "scale_x2": data.whatif_scale,
        }
    if data.validation_errors:
        payload["errors"] = data.validation_errors
    return payload


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def _ai_meta(data: ExplainData, now: Optional[str]) -> Dict[str, Any]:
    from infra.version import __version__

    meta: Dict[str, Any] = {
        "language": "infra-lang",
        "generator_version": __version__,
        "checksum": data.checksum,
    }
    if now is not None:
        meta["timestamp"] = now
    return meta


def _ai_banner(data: ExplainData, now: Optional[str]) -> str:
    """One-line machine-oriented banner prepended to text/markdown output."""
    meta = _ai_meta(data, now)
    meta_txt = " ".join(f"{k}={v}" for k, v in meta.items())
    summary_txt = " ".join(data.summary_sentences)
    return f"[meta] {meta_txt}\n[summary] {summary_txt}\n\n"


def render_explain(
    data: ExplainData,
    *,
    output_format: str = "markdown",
    audience: str = "human",
    sections: Sequence[str] = tuple(SECTION_TITLES),
    now: Optional[str] = None,
) -> str:
    """Render the insight report.

    ``now`` is an ISO-8601 timestamp injected by the CLI; when ``None`` the
    ``_meta.timestamp`` field is omitted (used by deterministic tests).
    """
    if output_format == "json":
        payload = _json_payload(data, sections)
        if audience == "ai":
            payload["_meta"] = _ai_meta(data, now)
            payload["_summary"] = data.summary_sentences
            return json.dumps(payload, separators=(",", ":"))
        return json.dumps(payload, indent=2) + "\n"

    banner = _ai_banner(data, now) if audience == "ai" else ""
    if output_format == "markdown":
        out: List[str] = [f"# Architecture Insight: {data.project}", ""]
        for sec in sections:
            _MD_SECTIONS[sec](data, out)
        if data.validation_errors:
            out += ["## Errors", ""]
            for e in data.validation_errors:
                loc = _loc(e)
                loc_txt = f" ({loc})" if loc else ""
                out.append(f"- `{e['code']}`{loc_txt} {e['message']}")
            out.append("")
        return banner + "\n".join(out)

    # plain text
    out = [f"ARCHITECTURE INSIGHT: {data.project}", ""]
    for sec in sections:
        _TXT_SECTIONS[sec](data, out)
    if data.validation_errors:
        _txt_header("Errors", out)
        for e in data.validation_errors:
            loc = _loc(e)
            loc_txt = f" [{loc}]" if loc else ""
            out.append(f"{e['code']}{loc_txt}: {e['message']}")
        out.append("")
    return banner + "\n".join(out)


def parse_sections(raw: str) -> List[str]:
    """Expand a ``--sections`` value into validated section ids.

    Accepts ``all`` or a comma-separated list. Raises ``ValueError`` on an
    unknown section name.
    """
    from infra.explain import SECTION_IDS

    if raw.strip().lower() == "all":
        return list(SECTION_IDS)
    chosen = [s.strip().lower() for s in raw.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in SECTION_IDS]
    if unknown:
        raise InvalidSectionsError(
            message=f"Unknown section(s): {', '.join(unknown)}. "
            f"Valid: {', '.join(SECTION_IDS)}"
        )
    if not chosen:
        raise InvalidSectionsError(
            message="--sections produced an empty selection"
        )
    # keep canonical order even when the user lists them out of order
    return [s for s in SECTION_IDS if s in set(chosen)]
