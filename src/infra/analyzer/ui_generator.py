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
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from infra.analyzer.cost import CostEstimate
from infra.analyzer.drift import DriftReport
from infra.parser import ast_nodes as n
from infra.version import __version__

#: Public alias — the "infrastructure spec" consumed by the UI generator is
#: the parsed Infra program (the complete AST of a ``.infra`` file).
InfrastructureSpec = n.Program

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
            '<p class="drift-error">Drift probe unavailable: '
            f"{_esc(report.error)}</p>",
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
<h2>Architecture DAG</h2>
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
