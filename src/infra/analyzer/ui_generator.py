"""Standalone HTML dashboard / report generator (v0.5.2).

Renders a single-file, fully offline HTML page containing:

* **Architecture DAG** — services, databases, caches and queues connected by
  ``depends_on`` / ``depends`` edges, with networks, secret stores and
  network policies placed in a shared-infrastructure lane;
* **FinOps calculator** — the monthly cost estimate table plus a
  cost-share bar chart;
* **Live-drift panel** — In-Sync / Drifted state with per-field highlights
  (rendered when a :class:`DriftReport` is supplied);
* **Environment selector** — preview switcher for the environment overlays
  declared in the file.

The generated document inlines all CSS and JavaScript and references **no**
external resource (no CDN, no web fonts, no remote images), so it works
completely offline and inside sandboxed viewers.
"""

from __future__ import annotations

import html
from base64 import b64encode as _b64encode
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypeAlias
from urllib.parse import quote as _urlquote

from infra.analyzer.cost import CostEstimate, estimate_cost
from infra.analyzer.drift import DriftReport
from infra.analyzer.environments import apply_environment_overlay
from infra.parser import ast_nodes as n
from infra.version import __version__

#: Public alias — the "infrastructure spec" consumed by the UI generator is
#: the parsed Infra program (the complete AST of a ``.infra`` file).
#: Declared as an explicit ``TypeAlias`` so that mypy treats it as a type in
#: every invocation mode (plain ``mypy src/infra`` did not promote the bare
#: assignment to an alias and reported "Variable ... is not valid as a type").
InfrastructureSpec: TypeAlias = n.Program

_KIND_COLORS: Dict[str, str] = {
    "service": "#2563eb",
    "database": "#7c3aed",
    "cache": "#0891b2",
    "queue": "#b45309",
    "external": "#64748b",
}

_LANE_COLORS: Dict[str, str] = {
    "network": "#059669",
    "secret_store": "#dc2626",
    "network_policy": "#d97706",
}

_NODE_W = 168
_NODE_H = 46
_DX = 232  # horizontal pitch between DAG layers
_DY = 92  # vertical pitch inside a DAG column
_LANE_DY = 62  # vertical pitch inside the shared-infrastructure lane
_MARGIN = 30


@dataclass(frozen=True)
class _DagNode:
    """One drawable node of the architecture graph."""

    name: str
    kind: str
    sub: str
    lane: bool = False
    x: float = 0.0
    y: float = 0.0


def _esc(value: str) -> str:
    """HTML-escape a user-controlled string (text and attribute contexts)."""
    return html.escape(value, quote=True)


def _collect_dag(
    spec: InfrastructureSpec,
) -> Tuple[List[_DagNode], List[Tuple[str, str]]]:
    """Collect workload nodes + dependency edges and shared-lane nodes."""
    workloads: List[_DagNode] = []
    lane: List[_DagNode] = []
    edges: List[Tuple[str, str]] = []
    for stmt in spec.statements:
        if isinstance(stmt, n.ServiceDef):
            workloads.append(_DagNode(stmt.name, "service", "service"))
            for dep in stmt.dependencies:
                edges.append((stmt.name, dep))
        elif isinstance(stmt, n.DatabaseDef):
            workloads.append(_DagNode(stmt.name, "database", stmt.type))
        elif isinstance(stmt, n.CacheDef):
            workloads.append(_DagNode(stmt.name, "cache", stmt.type))
        elif isinstance(stmt, n.QueueDef):
            workloads.append(_DagNode(stmt.name, "queue", stmt.type))
        elif isinstance(stmt, n.NetworkDef):
            lane.append(_DagNode(stmt.name, "network", stmt.cidr or "network", True))
        elif isinstance(stmt, n.SecretStoreDef):
            lane.append(
                _DagNode(stmt.name, "secret_store", stmt.provider or "store", True)
            )
        elif isinstance(stmt, n.NetworkPolicyDef):
            sub = f"target: {stmt.target}" if stmt.target else "policy"
            lane.append(_DagNode(stmt.name, "network_policy", sub, True))
    # Ghost nodes for edge endpoints not declared in *this* file (imported or
    # forward-referenced workloads), so every edge still lands on a node.
    declared = {node.name for node in workloads} | {node.name for node in lane}
    for _src, dst in edges:
        if dst not in declared:
            workloads.append(_DagNode(dst, "external", "external"))
            declared.add(dst)
    return [*lane, *workloads], edges


def _layout(nodes: List[_DagNode], edges: List[Tuple[str, str]]) -> None:
    """Assign (x, y) coordinates via longest-path DAG layering."""
    deps: Dict[str, List[str]] = {}
    for src, dst in edges:
        deps.setdefault(src, []).append(dst)
    depth_memo: Dict[str, int] = {}
    visiting: set[str] = set()

    def depth(name: str) -> int:
        if name in depth_memo:
            return depth_memo[name]
        if name in visiting:  # cycle guard: break the recursion, not the report
            return 0
        visiting.add(name)
        targets = deps.get(name)
        layer = 1 + max((depth(t) for t in targets), default=-1) if targets else 0
        visiting.discard(name)
        depth_memo[name] = layer
        return layer

    lane_nodes = [nd for nd in nodes if nd.lane]
    dag_nodes = [nd for nd in nodes if not nd.lane]
    lane_offset = 1 if lane_nodes else 0
    max_depth = max((depth(nd.name) for nd in dag_nodes), default=0)
    # Deeper (most dependent) nodes sit in the leftmost workload column;
    # their dependencies flow to the right. Column index = max_depth - depth.
    columns: Dict[int, int] = {}
    laid: List[_DagNode] = []
    for i, nd in enumerate(lane_nodes):
        laid.append(
            _DagNode(
                nd.name, nd.kind, nd.sub, True,
                float(_MARGIN), float(_MARGIN + i * _LANE_DY),
            )
        )
    for nd in dag_nodes:
        col = max_depth - depth(nd.name)
        row = columns.get(col, 0)
        columns[col] = row + 1
        laid.append(
            _DagNode(
                nd.name, nd.kind, nd.sub, False,
                float(_MARGIN + (col + lane_offset) * _DX),
                float(_MARGIN + row * _DY),
            )
        )
    nodes[:] = laid


def _canvas_size(
    nodes: Sequence[_DagNode], edges: Sequence[Tuple[str, str]]
) -> Tuple[int, int]:
    """Overall SVG canvas size that fits every laid-out node with margins."""
    del edges  # sizing depends on nodes only; kept in the signature for clarity
    if not nodes:
        return (2 * _MARGIN + 320, 2 * _MARGIN + 80)
    right = max(nd.x + _NODE_W for nd in nodes) + _MARGIN
    bottom = max(nd.y + _NODE_H for nd in nodes) + _MARGIN
    return (int(right), int(bottom))


def _node_svg(nd: _DagNode) -> str:
    """One ``<g>`` element: colored rounded card with name and kind caption."""
    color = _LANE_COLORS.get(nd.kind) or _KIND_COLORS.get(nd.kind, "#334155")
    return (
        f'<g class="node node-{_esc(nd.kind)}" data-name="{_esc(nd.name)}" '
        f'transform="translate({nd.x:.0f},{nd.y:.0f})">'
        f'<rect width="{_NODE_W}" height="{_NODE_H}" rx="8" '
        f'style="fill:{color}"/>'
        f'<text class="node-name" x="12" y="20">{_esc(nd.name)}</text>'
        f'<text class="node-kind" x="12" y="37">{_esc(nd.kind)}'
        f'{" · " + _esc(nd.sub) if nd.sub != nd.kind else ""}</text>'
        f"</g>"
    )


def _dag_svg(nodes: List[_DagNode], edges: List[Tuple[str, str]]) -> str:
    """Full ``<svg>`` for the architecture DAG (arrows + node cards)."""
    if not nodes:
        return '<p class="empty-note">No workloads declared in this file.</p>'
    width, height = _canvas_size(nodes, edges)
    by_name = {nd.name: nd for nd in nodes}
    parts: List[str] = [
        f'<svg class="dag-svg" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Architecture DAG" '
        f'width="{width}" height="{height}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" class="arrow-head"/></marker></defs>',
    ]
    for src, dst in edges:
        a, b = by_name.get(src), by_name.get(dst)
        if a is None or b is None:  # defensive: unknown endpoint
            continue
        x1, y1 = a.x + _NODE_W, a.y + _NODE_H / 2
        x2, y2 = b.x, b.y + _NODE_H / 2
        parts.append(
            f'<line class="edge" data-from="{_esc(src)}" data-to="{_esc(dst)}" '
            f'x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            'marker-end="url(#arrow)"/>'
        )
    parts.extend(_node_svg(nd) for nd in nodes)
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------- #
# Standalone SVG DAG export (v0.5.5)
# ---------------------------------------------------------------------- #

_SVG_FONT = 'font-family="system-ui,Segoe UI,Roboto,sans-serif"'


def _dag_svg_standalone(nodes: List[_DagNode], edges: List[Tuple[str, str]]) -> str:
    """Self-contained ``.svg`` document (XML header + inline styles)."""
    width, height = _canvas_size(nodes, edges)
    by_name = {nd.name: nd for nd in nodes}
    parts: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        'aria-label="Architecture DAG">',
        f'<rect width="{width}" height="{height}" fill="#f8fafc"/>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>',
    ]
    for src, dst in edges:
        a, b = by_name.get(src), by_name.get(dst)
        if a is None or b is None:  # defensive: unknown endpoint
            continue
        x1, y1 = a.x + _NODE_W, a.y + _NODE_H / 2
        x2, y2 = b.x, b.y + _NODE_H / 2
        parts.append(
            f'<line data-from="{_esc(src)}" data-to="{_esc(dst)}" '
            f'x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            'stroke="#94a3b8" stroke-width="1.6" marker-end="url(#arrow)"/>'
        )
    for nd in nodes:
        color = _LANE_COLORS.get(nd.kind) or _KIND_COLORS.get(nd.kind, "#334155")
        sub = f" · {_esc(nd.sub)}" if nd.sub != nd.kind else ""
        parts.append(
            f'<g data-name="{_esc(nd.name)}" data-kind="{_esc(nd.kind)}" '
            f'transform="translate({nd.x:.0f},{nd.y:.0f})">'
            f'<rect width="{_NODE_W}" height="{_NODE_H}" rx="8" fill="{color}"/>'
            f'<text x="12" y="20" {_SVG_FONT} fill="#ffffff" font-size="13" '
            f'font-weight="600">{_esc(nd.name)}</text>'
            f'<text x="12" y="37" {_SVG_FONT} fill="#e2e8f0" font-size="10">'
            f"{_esc(nd.kind)}{sub}</text>"
            "</g>"
        )
    if not nodes:
        parts.append(
            f'<text x="{_MARGIN}" y="{_MARGIN}" {_SVG_FONT} fill="#475569" '
            'font-size="14">No workloads declared.</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def generate_dag_svg(spec: InfrastructureSpec) -> str:
    """Render the architecture DAG of *spec* as a standalone ``.svg`` file.

    Same collector and longest-path layout as the dashboard, but the output
    inlines all styles, so it opens in any browser/viewer with no external
    resources. (PNG/PDF rasterization is intentionally deferred — it would
    pull in a native rasterizer dependency.)
    """
    nodes, edges = _collect_dag(spec)
    _layout(nodes, edges)
    return _dag_svg_standalone(nodes, edges)


_DAG_LINK_STYLE = (
    "display:inline-block;margin-left:12px;padding:2px 10px;"
    "border:1px solid #cbd5e1;border-radius:6px;background:#ffffff;"
    "color:#0f172a;text-decoration:none;font-size:12px;font-weight:600;"
    "vertical-align:middle"
)


def _dag_png_download_link(spec: InfrastructureSpec) -> str:
    """``Download PNG`` anchor with a base64 data URI of the rendered DAG.

    Pillow is imported lazily (keeping the cycle-graph_png ↔ ui_generator
    out of module import time); when Pillow is unavailable the link is
    simply omitted and the dashboard still ships the SVG variant.
    """
    try:
        from infra.analyzer.graph_png import render_dag_png_bytes
    except ImportError:
        return ""
    uri = _b64encode(render_dag_png_bytes(spec)).decode("ascii")
    return (
        f'<a download="infra-dag.png" style="{_DAG_LINK_STYLE}" '
        f'href="data:image/png;base64,{uri}">Download PNG</a>'
    )


def _dag_download_link(spec: InfrastructureSpec) -> str:
    """``Download SVG``/``Download PNG`` anchors embedding data URIs."""
    uri = _urlquote(generate_dag_svg(spec), safe="")
    return (
        f'<a download="infra-dag.svg" style="{_DAG_LINK_STYLE}" '
        f'href="data:image/svg+xml;charset=utf-8,{uri}">Download SVG</a>'
        f"{_dag_png_download_link(spec)}"
    )


# ---------------------------------------------------------------------- #
# Side-by-side environment comparison (v0.5.5)
# ---------------------------------------------------------------------- #


def _expr_text(value: Any) -> str:
    """Flatten a literal-ish AST expression to short display text."""
    if value is None:
        return ""
    if isinstance(value, n.Literal):
        return str(value.value)
    if isinstance(value, n.Identifier):
        return value.name
    if isinstance(value, n.TemplateString):
        return "".join(p if isinstance(p, str) else "…" for p in value.parts)
    return str(value)


def _env_var_text(entry: n.EnvEntry) -> str:
    """Display text for one env var (value or its advisory source reference)."""
    if entry.value is not None:
        return _expr_text(entry.value)
    if entry.from_secret:
        return f"from secret {entry.from_secret}"
    if entry.from_config:
        return f"from configmap {entry.from_config}"
    if entry.from_field:
        return f"from field {entry.from_field}"
    if entry.from_env:
        return f"from env {entry.from_env}"
    return ""


_CompareRow = Tuple[str, str, str, str, str]  # (badge, service, field, old, new)


def _workload_summary(spec: InfrastructureSpec) -> Dict[str, Dict[str, Any]]:
    """Key parameters of every workload, keyed by name (compare panels/diff)."""
    out: Dict[str, Dict[str, Any]] = {}
    for stmt in spec.statements:
        if isinstance(stmt, n.ServiceDef):
            resources: Dict[str, str] = {}
            if stmt.resources is not None:
                for section in ("requests", "limits"):
                    data = getattr(stmt.resources, section, None)
                    if data is None:
                        continue
                    if data.cpu is not None:
                        resources[f"{section}.cpu"] = data.cpu.to_kubernetes()
                    if data.memory is not None:
                        resources[f"{section}.memory"] = (
                            data.memory.to_kubernetes()
                        )
            out[stmt.name] = {
                "kind": "service",
                "image": stmt.image
                or ("dockerfile build" if stmt.build else "(none)"),
                "replicas": stmt.replicas,
                "ports": ",".join(
                    sorted(
                        f"{p.host}:{p.target}"
                        if p.host and p.target
                        else str(p.target or p.host)
                        for p in stmt.ports
                    )
                ),
                "expose": str(bool(getattr(stmt, "expose", False))).lower(),
                "env": {e.name: _env_var_text(e) for e in stmt.env},
                "resources": resources,
                "storage": "",
            }
        elif isinstance(stmt, (n.DatabaseDef, n.CacheDef, n.QueueDef)):
            kind = type(stmt).__name__.replace("Def", "").lower()
            storage = ""
            if isinstance(stmt, n.DatabaseDef):
                volume = stmt.storage or stmt.size
                if volume is not None:
                    storage = volume.to_kubernetes()
            out[stmt.name] = {
                "kind": kind,
                "image": f"{stmt.type}:{stmt.version or 'latest'}",
                "replicas": stmt.replicas,
                "ports": "",
                "expose": "false",
                "env": {},
                "resources": {},
                "storage": storage,
            }
    return out


def _workload_line(meta: Dict[str, Any]) -> str:
    short = f"{meta['kind']} · {meta['image']} · replicas: {meta['replicas']}"
    if meta.get("storage"):
        short += f" · {meta['storage']}"
    return short


_DIFF_FIELDS = ("kind", "image", "replicas", "ports", "expose", "storage")


def _diff_workloads(
    wa: Dict[str, Dict[str, Any]], wb: Dict[str, Dict[str, Any]]
) -> List[_CompareRow]:
    """Per-service comparison rows: added / removed / changed (field-level)."""
    rows: List[_CompareRow] = []
    for name in sorted(set(wb) - set(wa)):
        rows.append(("added", name, "", "", _workload_line(wb[name])))
    for name in sorted(set(wa) - set(wb)):
        rows.append(("removed", name, "", _workload_line(wa[name]), ""))
    for name in sorted(set(wa) & set(wb)):
        ma, mb = wa[name], wb[name]
        for field in _DIFF_FIELDS:
            va, vb = ma[field], mb[field]
            if va != vb:
                rows.append(("changed", name, field, str(va or "—"), str(vb or "—")))
        for prefix in ("env", "resources"):
            da: Dict[str, str] = ma[prefix]
            db: Dict[str, str] = mb[prefix]
            for key in sorted(set(da) | set(db)):
                va, vb = da.get(key, "—"), db.get(key, "—")
                if va != vb:
                    rows.append(("changed", name, f"{prefix}.{key}", va, vb))
    return rows


def _fmt_cell(text: str) -> str:
    return _esc(text) if text else '<span class="cmp-na">—</span>'


def _compare_summary_html(rows: List[_CompareRow], env_a: str, env_b: str) -> str:
    if not rows:
        return (
            '<p class="cmp-empty">No differences between '
            f"<b>{_esc(env_a)}</b> and <b>{_esc(env_b)}</b> "
            "— the environments deploy identical workloads.</p>"
        )
    lis = [
        '<table class="cmp-table"><thead><tr>'
        "<th></th><th>Service</th><th>Field</th>"
        f"<th>{_esc(env_a)}</th><th>{_esc(env_b)}</th></tr></thead><tbody>"
    ]
    words = {"added": "+", "removed": "−", "changed": "Δ"}
    for badge, name, field, old, new in rows:
        lis.append(
            f'<tr class="cmp-{badge}">'
            f'<td class="cmp-badge">{words[badge]}</td>'
            f"<td>{_esc(name)}</td>"
            f"<td>{_esc(field) if field else '&nbsp;'}</td>"
            f"<td>{_fmt_cell(old)}</td><td>{_fmt_cell(new)}</td></tr>"
        )
    lis.append("</tbody></table>")
    return "".join(lis)


def _env_panel_html(
    env_name: str,
    workloads: Dict[str, Dict[str, Any]],
    estimate: CostEstimate,
    changed: Dict[str, set[str]],
) -> str:
    rows_html: List[str] = []
    for name in sorted(workloads):
        meta = workloads[name]
        svc_changed = changed.get(name, set())

        def cell(field: str, value: Any, mono: bool = False) -> str:
            text = _esc(str(value) if value not in (None, "") else "—")
            cls = ' class="cmp-hl"' if field in svc_changed else ""
            if mono:
                text = f"<code>{text}</code>"
            return f"<td{cls}>{text}</td>"

        env_bits = ", ".join(
            f"{k}={v}" for k, v in sorted(meta["env"].items())
        )
        res_bits = ", ".join(
            f"{k}: {v}" for k, v in sorted(meta["resources"].items())
        )
        rows_html.append(
            f"<tr><td><b>{_esc(name)}</b><br><small>{_esc(meta['kind'])}</small></td>"
            + cell("image", meta["image"], mono=True)
            + cell("replicas", meta["replicas"])
            + cell("env", env_bits or "—")
            + cell("resources", res_bits or "—")
            + "</tr>"
        )
    if not rows_html:
        rows_html.append(
            '<tr><td colspan="5" class="cmp-na">(no workloads)</td></tr>'
        )
    return (
        f'<section class="cmp-panel"><h3>{_esc(env_name)}</h3>'
        f'<p class="cmp-cost">estimated: <b>${estimate.total_monthly_usd}/mo</b></p>'
        '<table class="cmp-wl"><thead><tr><th>Service</th><th>Image</th>'
        "<th>Replicas</th><th>Env</th><th>Resources</th></tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></section>"
    )


def generate_compare_html(spec: InfrastructureSpec, env_a: str, env_b: str) -> str:
    """Render a single-file side-by-side comparison of two environments.

    The program is parsed once; each side applies its own overlay (the special
    name ``base`` keeps the unoverlaid file). Raises
    :class:`EnvironmentNotFoundError` for unknown overlay names — callers
    decide how to present the error.
    """

    def _select(name: str) -> n.Program:
        if name == "base":
            return spec
        merged = apply_environment_overlay(spec, name)
        # Same quirk handling as the dashboard: applying the overlay strips
        # the overlay list; restore it so selectors/diff keep full context.
        return _dc_replace(merged, environments=spec.environments)

    prog_a, prog_b = _select(env_a), _select(env_b)
    wa, wb = _workload_summary(prog_a), _workload_summary(prog_b)
    est_a, est_b = estimate_cost(prog_a), estimate_cost(prog_b)
    rows = _diff_workloads(wa, wb)
    if est_a.total_monthly_usd != est_b.total_monthly_usd:
        rows.append(
            (
                "changed",
                "(finops)",
                "est. monthly",
                f"${est_a.total_monthly_usd}/mo",
                f"${est_b.total_monthly_usd}/mo",
            )
        )
    changed: Dict[str, set[str]] = {}
    for _badge, name, field, _old, _new in rows:
        changed.setdefault(name, set()).add(field.split(".", 1)[0])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Infra Lang — compare {html.escape(env_a)} vs {html.escape(env_b)}</title>
<style>{_COMPARE_CSS}</style>
</head>
<body>
<header class="page-header">
<h1>Infra Lang — environment comparison</h1>
<p class="page-meta">infra-lang v{__version__} · <b>{_esc(env_a)}</b> vs
 <b>{_esc(env_b)}</b> · (+) added in {html.escape(env_b)},
 (−) removed from {html.escape(env_b)}, (Δ) changed</p>
</header>
<main>
<h2>Diff summary</h2>
{_compare_summary_html(rows, env_a, env_b)}
<div class="cmp-grid">
{_env_panel_html(env_a, wa, est_a, changed)}
{_env_panel_html(env_b, wb, est_b, changed)}
</div>
</main>
<footer class="page-footer">Generated by infra-lang v{__version__}
 — fully offline, single-file report.</footer>
</body>
</html>
"""


_COMPARE_CSS = """
:root { --fg:#0f172a; --muted:#64748b; --line:#e2e8f0; --bg:#f8fafc; }
* { box-sizing:border-box; }
body { margin:0; color:var(--fg); background:var(--bg);
  font-family:system-ui,Segoe UI,Roboto,sans-serif; }
.page-header { padding:20px 28px 10px; background:#fff;
  border-bottom:1px solid var(--line); }
.page-header h1 { margin:0 0 4px; font-size:20px; }
.page-meta { margin:0; color:var(--muted); font-size:13px; }
main { padding:20px 28px; }
h2 { font-size:15px; margin:0 0 10px; }
.cmp-empty { padding:14px 16px; background:#ecfdf5; color:#065f46;
  border:1px solid #a7f3d0; border-radius:8px; }
.cmp-table, .cmp-wl { width:100%; border-collapse:collapse; background:#fff;
  border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.cmp-table th, .cmp-table td, .cmp-wl th, .cmp-wl td { padding:7px 10px;
  border-bottom:1px solid var(--line); text-align:left;
  font-size:13px; vertical-align:top; }
.cmp-table th, .cmp-wl th { background:#f1f5f9; font-size:12px;
  text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
.cmp-badge { font-weight:700; text-align:center; width:34px; }
.cmp-added .cmp-badge { color:#059669; }
.cmp-removed .cmp-badge { color:#dc2626; }
.cmp-changed .cmp-badge { color:#d97706; }
.cmp-added td { background:#f0fdf4; }
.cmp-removed td { background:#fef2f2; }
.cmp-changed td { background:#fffbeb; }
.cmp-grid { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:18px; }
@media (max-width: 900px) { .cmp-grid { grid-template-columns:1fr; } }
.cmp-panel h3 { margin:0 0 6px; font-size:16px; }
.cmp-cost { margin:0 0 8px; color:var(--muted); font-size:13px; }
.cmp-hl { outline:2px solid #f59e0b; outline-offset:-2px; }
.cmp-na { color:var(--muted); }
code { background:#f1f5f9; padding:1px 4px; border-radius:4px; }
.page-footer { padding:14px 28px; color:var(--muted); font-size:12px; }
"""


def _finops_html(estimate: CostEstimate) -> str:
    """FinOps panel: breakdown table + monthly total + share bar chart."""
    if not estimate.items:
        return '<p class="empty-note">No billable resources in this file.</p>'
    total = estimate.total_monthly_usd
    rows: List[str] = []
    bars: List[str] = []
    for item in estimate.items:
        share = (item.monthly_usd / total * 100.0) if total > 0 else 0.0
        rows.append(
            f"<tr><td>{_esc(item.name)}</td><td>{_esc(item.kind)}</td>"
            f'<td class="num">{item.vcpu:.2f}</td>'
            f'<td class="num">{item.ram_gb:.2f}</td>'
            f'<td class="num">{item.storage_gb:.2f}</td>'
            f'<td class="num">{item.monthly_usd:.2f}</td>'
            f'<td class="num">{share:.1f}%</td></tr>'
        )
        bars.append(
            f'<div class="bar-row" data-resource="{_esc(item.name)}">'
            f'<span class="bar-label">{_esc(item.name)}</span>'
            f'<span class="bar" style="width:{max(2.0, min(100.0, share)):.1f}%">'
            "</span>"
            f'<span class="bar-value">{item.monthly_usd:.2f} USD'
            f" ({share:.1f}%)</span></div>"
        )
    return (
        '<div class="finops-total">Estimated monthly total: '
        f'<b>{total:.2f} USD</b></div>'
        '<table class="finops-table">'
        "<thead><tr><th>Resource</th><th>Kind</th>"
        '<th class="num">vCPU</th><th class="num">RAM (GB)</th>'
        '<th class="num">Storage (GB)</th><th class="num">Monthly (USD)</th>'
        '<th class="num">Share</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
        f'<div class="finops-chart">{"".join(bars)}</div>'
        '<p class="finops-note">Static order-of-magnitude estimate '
        "(`infra cost`). Not a billing-grade quote.</p>"
    )


def _probe_error_badge(error: str) -> str:
    """Classify a live-probe error into a readable badge label (fail-safe UI).

    ``timed out`` → the cluster/daemon did not answer in time; ``not
    available`` → the CLI tool (``kubectl``/``docker``) is missing or its
    daemon is down; ``unreachable`` → the endpoint exists but cannot be
    reached. Anything else is a generic probe error.
    """
    low = error.lower()
    if "timed out" in low:
        return "PROBE TIMEOUT"
    if "not available" in low:
        return "CLI TOOL MISSING"
    if "unreachable" in low:
        return "CLUSTER UNREACHABLE"
    return "PROBE ERROR"


def _drift_html(report: Optional[DriftReport]) -> Tuple[str, str]:
    """Drift panel body and the machine-readable state (``data-state``)."""
    if report is None:
        return (
            '<p class="empty-note">Live drift was not collected for this '
            "report. Run <code>infra doctor --drift</code> for a live probe."
            "</p>",
            "none",
        )
    if report.error is not None:
        return (
            f'<p class="badge badge-err">{_probe_error_badge(report.error)}</p>'
            '<p class="drift-error">Live drift probe '
            f"({_esc(report.target)}): {_esc(report.error)}</p>",
            "error",
        )
    if not report.has_drift:
        synced = ", ".join(_esc(name) for name in report.in_sync) or "none"
        return (
            '<p class="badge badge-ok">IN-SYNC</p>'
            f'<p class="drift-note">Verified in sync: {synced}</p>',
            "clean",
        )
    rows = []
    for item in report.items:
        rows.append(
            f'<tr class="drift-row" data-resource="{_esc(item.resource)}">'
            f"<td>{_esc(item.resource)}</td>"
            f'<td class="drift-field">{_esc(item.parameter)}</td>'
            f'<td class="drift-exp">{_esc(item.expected)}</td>'
            f'<td class="drift-live">{_esc(item.live)}</td>'
            f'<td class="drift-status">{_esc(item.status)}</td></tr>'
        )
    return (
        '<p class="badge badge-warn">DRIFTED</p>'
        '<table class="drift-table"><thead><tr><th>Resource</th>'
        "<th>Parameter</th><th>Expected</th><th>Live</th><th>Status</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>",
        "drifted",
    )


@dataclass(frozen=True)
class _EnvInfo:
    """Summary of one selectable environment for the preview switcher."""

    name: str
    details: str


def _env_infos(spec: InfrastructureSpec) -> List[_EnvInfo]:
    """Collect selector entries from ``environment`` defs and overlay specs."""
    infos: List[_EnvInfo] = []
    for stmt in spec.statements:
        if isinstance(stmt, n.EnvironmentDef):
            bits = [
                f"{label}: {value}"
                for label, value in (
                    ("provider", stmt.provider),
                    ("region", stmt.region),
                    ("namespace", stmt.namespace),
                )
                if value
            ]
            infos.append(_EnvInfo(stmt.name, ", ".join(bits) or "environment"))
    for env_spec in spec.environments:
        count = len(env_spec.overrides)
        noun = "override" if count == 1 else "overrides"
        infos.append(_EnvInfo(env_spec.name, f"{count} service {noun}"))
    return infos


def _env_selector_html(
    spec: InfrastructureSpec, env_name: Optional[str]
) -> str:
    """Environment preview switcher (``<select>`` + per-env info blocks)."""
    infos = _env_infos(spec)
    if not infos:
        return '<p class="empty-note">No environments declared.</p>'
    options = ['<option value="base">base</option>']
    blocks = [
        '<div class="env-info" data-env="base">'
        "Base specification without an environment overlay.</div>"
    ]
    for info in infos:
        selected = ' selected' if env_name == info.name else ""
        options.append(
            f'<option value="{_esc(info.name)}"{selected}>'
            f"{_esc(info.name)}</option>"
        )
        hidden = "" if env_name == info.name else ' hidden'
        blocks.append(
            f'<div class="env-info" data-env="{_esc(info.name)}"{hidden}>'
            f"{_esc(info.details)}</div>"
        )
    active = env_name or "base"
    return (
        '<label for="env-select">Environment:</label>'
        f'<select id="env-select">{"".join(options)}</select>'
        f'<span id="env-active" class="env-active">{_esc(active)}</span>'
        f'{"".join(blocks)}'
    )


def generate_ui_html(
    spec: InfrastructureSpec,
    cost_estimate: CostEstimate,
    drift_report: Optional[DriftReport] = None,
    env_name: Optional[str] = None,
) -> str:
    """Render the complete standalone dashboard HTML page.

    Parameters:
        spec: parsed Infra program (the infrastructure specification).
        cost_estimate: monthly cost estimate for *spec*.
        drift_report: optional live-drift report to display.
        env_name: environment overlay selected for this view (pre-selects
            the environment switcher and is shown in the header).

    Returns a single self-contained HTML document (inline CSS/JS, no
    external references — safe for offline use and sandboxed viewers).
    """
    dag_nodes, dag_edges = _collect_dag(spec)
    _layout(dag_nodes, dag_edges)
    drift_body, drift_state = _drift_html(drift_report)
    active_env = env_name or "base"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Infra Lang Dashboard</title>
<style>{_PAGE_CSS}</style>
</head>
<body data-active-env="{_esc(active_env)}">
<header class="page-header">
<h1>Infra Lang Dashboard</h1>
<p class="page-meta">infra-lang v{__version__}
 · environment: <b>{_esc(active_env)}</b></p>
</header>
<nav class="tabs">
<button class="tab active" data-tab="dag">Architecture</button>
<button class="tab" data-tab="finops">FinOps</button>
<button class="tab" data-tab="drift">Drift</button>
<span class="env-slot">{_env_selector_html(spec, env_name)}</span>
</nav>
<main>
<section id="panel-dag" class="panel active">
<h2>Architecture DAG{_dag_download_link(spec)}</h2>
{_dag_svg(dag_nodes, dag_edges)}
</section>
<section id="panel-finops" class="panel">
<h2>FinOps Calculator</h2>
{_finops_html(cost_estimate)}
</section>
<section id="panel-drift" class="panel" data-state="{drift_state}">
<h2>Live Drift</h2>
{drift_body}
</section>
</main>
<footer class="page-footer">Generated by infra-lang v{__version__}
 — fully offline, single-file report.</footer>
<script>{_PAGE_JS}</script>
</body>
</html>
"""


_PAGE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
 background: #f1f5f9; color: #0f172a; }
.page-header { background: #0f172a; color: #f8fafc; padding: 18px 28px; }
.page-header h1 { margin: 0; font-size: 20px; }
.page-meta { margin: 4px 0 0; font-size: 12px; color: #94a3b8; }
.tabs { display: flex; gap: 8px; align-items: center; padding: 10px 28px;
 background: #ffffff; border-bottom: 1px solid #e2e8f0; }
.tab { border: 1px solid #cbd5e1; background: #ffffff; padding: 6px 14px;
 border-radius: 6px; cursor: pointer; font-size: 13px; }
.tab.active { background: #2563eb; border-color: #2563eb; color: #ffffff; }
.env-slot { margin-left: auto; font-size: 13px; display: inline-flex;
 gap: 8px; align-items: center; }
.env-active { color: #2563eb; font-weight: 600; }
.env-info { color: #475569; font-size: 12px; }
main { padding: 20px 28px; }
.panel { display: none; }
.panel.active { display: block; background: #ffffff; border: 1px solid
 #e2e8f0; border-radius: 10px; padding: 18px; }
.panel h2 { margin: 0 0 14px; font-size: 16px; }
.dag-svg .edge { stroke: #94a3b8; stroke-width: 1.6; }
.dag-svg .arrow-head { fill: #94a3b8; }
.dag-svg .node rect { filter: drop-shadow(0 1px 2px rgba(15, 23, 42, .25)); }
.dag-svg .node-name { fill: #ffffff; font-size: 13px; font-weight: 600; }
.dag-svg .node-kind { fill: #e2e8f0; font-size: 10px; }
.dag-svg .node.hl rect { stroke: #facc15; stroke-width: 3; }
.dag-svg .edge.hl { stroke: #f59e0b; stroke-width: 2.6; }
.finops-total { font-size: 15px; margin-bottom: 12px; }
.finops-table, .drift-table { border-collapse: collapse; width: 100%;
 font-size: 13px; margin-bottom: 16px; }
.finops-table th, .finops-table td, .drift-table th, .drift-table td {
 border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left; }
.finops-table thead, .drift-table thead { background: #f8fafc; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.finops-chart { margin: 8px 0 4px; }
.bar-row { display: flex; align-items: center; gap: 8px; margin: 4px 0;
 font-size: 12px; }
.bar-label { width: 140px; overflow: hidden; text-overflow: ellipsis;
 white-space: nowrap; }
.bar { display: inline-block; height: 14px; background: #2563eb;
 border-radius: 3px; min-width: 2px; }
.bar-value { color: #475569; }
.finops-note, .empty-note, .drift-note { color: #64748b; font-size: 12px; }
.badge { display: inline-block; padding: 4px 12px; border-radius: 999px;
 font-weight: 700; font-size: 12px; }
.badge-ok { background: #dcfce7; color: #166534; }
.badge-warn { background: #fef3c7; color: #92400e; }
.badge-err { background: #fee2e2; color: #991b1b; }
.drift-error { color: #b91c1c; font-size: 13px; }
.drift-exp { background: #dcfce7; }
.drift-live { background: #fee2e2; }
.drift-status { font-weight: 600; color: #92400e; }
.page-footer { padding: 14px 28px; color: #94a3b8; font-size: 11px; }
code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px; }
select { font-size: 13px; padding: 4px 6px; }
[hidden] { display: none; }
"""

_PAGE_JS = """
(function () {
  function activate(id) {
    document.querySelectorAll('.panel').forEach(function (p) {
      p.classList.toggle('active', p.id === 'panel-' + id);
    });
    document.querySelectorAll('.tab').forEach(function (t) {
      t.classList.toggle('active', t.getAttribute('data-tab') === id);
    });
  }
  document.querySelectorAll('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      activate(t.getAttribute('data-tab'));
    });
  });
  var sel = document.getElementById('env-select');
  if (sel) {
    sel.addEventListener('change', function () {
      document.body.setAttribute('data-active-env', sel.value);
      var label = document.getElementById('env-active');
      if (label) { label.textContent = sel.value; }
      document.querySelectorAll('.env-info').forEach(function (b) {
        b.hidden = b.getAttribute('data-env') !== sel.value;
      });
    });
  }
  document.querySelectorAll('.dag-svg .node').forEach(function (g) {
    var name = g.getAttribute('data-name');
    function mark(on) {
      g.classList.toggle('hl', on);
      document.querySelectorAll('.dag-svg .edge').forEach(function (e) {
        var hit = e.getAttribute('data-from') === name
          || e.getAttribute('data-to') === name;
        e.classList.toggle('hl', on && hit);
      });
    }
    g.addEventListener('mouseover', function () { mark(true); });
    g.addEventListener('mouseout', function () { mark(false); });
  });
})();
"""
