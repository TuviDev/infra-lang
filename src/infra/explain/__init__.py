"""Deterministic architecture-insight engine for ``infra explain`` (v0.9.0).

Collects a single :class:`ExplainData` snapshot from the *existing* analyzers
(cost, security, reliability, semantic validator) — zero new runtime
dependencies and zero AI/ML at runtime. All prose is generated from fixed
templates so the output is bit-for-bit reproducible for the same input.

The module is deliberately renderer-agnostic: :mod:`infra.explain.renderer`
turns the snapshot into ``markdown`` / ``text`` / ``json`` documents.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from infra.analyzer.cost import (
    GB_RAM_MONTHLY,
    GB_STORAGE_MONTHLY,
    MANAGED_DB_MONTHLY,
    VCPU_MONTHLY,
    CostEstimate,
    estimate_cost,
)
from infra.analyzer.reliability import ReliabilityChecker, ReliabilityFinding
from infra.analyzer.security import SecurityChecker
from infra.analyzer.validator import SemanticValidator
from infra.errors.exceptions import ValidationError, ValidationWarning
from infra.parser import ast_nodes as n

#: Sections that can be selected individually via ``--sections``.
SECTION_IDS: Tuple[str, ...] = (
    "overview",
    "services",
    "deps",
    "cost",
    "security",
    "reliability",
    "whatif",
)

#: Human-friendly display names for sections.
SECTION_TITLES: Dict[str, str] = {
    "overview": "Overview",
    "services": "Services",
    "deps": "Dependencies",
    "cost": "Cost Breakdown",
    "security": "Security Warnings",
    "reliability": "Reliability Report",
    "whatif": "What-If Scenarios",
}

#: Qualitative impact of each reliability rule (used in the report and in the
#: reliability score). Unknown future codes default to ``medium``.
REL_IMPACT: Dict[str, str] = {
    "REL001": "high",  # thundering herd on shared dependency
    "REL002": "medium",  # even replica count for HA database
    "REL003": "medium",  # no memory limit
    "REL004": "high",  # no health check
    "REL005": "medium",  # deep dependency chain
    "REL006": "high",  # database without backup
    "REL007": "high",  # single-replica service with dependents
    "REL008": "medium",  # cache without persistence
    "REL009": "low",  # no preStop hook
    "REL011": "medium",  # autoscale without limits
    "REL012": "low",  # autoscale together with fixed replicas
    "REL013": "medium",  # database without resources
    "REL014": "high",  # single-replica kafka
}

#: Weights used by :func:`reliability_score`.
_IMPACT_WEIGHT = {"high": 10, "medium": 5, "low": 2}

#: Number of top-cost items highlighted in the overview section.
_TOP_COSTS = 3


def _grade(findings_count: int) -> str:
    """Map a count of findings to a letter grade ``A``–``F``."""
    if findings_count <= 0:
        return "A"
    if findings_count == 1:
        return "B"
    if findings_count == 2:
        return "C"
    if findings_count == 3:
        return "D"
    if findings_count == 4:
        return "E"
    return "F"


def reliability_score(findings: List[ReliabilityFinding]) -> int:
    """Score reliability 0–100 from findings (100 = no findings).

    Impact weights: high = 10, medium = 5, low = 2. The score is clamped to
    0 so a pathological file never yields a negative value.
    """
    penalty = 0
    for f in findings:
        penalty += _IMPACT_WEIGHT.get(REL_IMPACT.get(f.code, "medium"), 5)
    return max(0, 100 - penalty)


def infer_arch_type(program: n.Program) -> str:
    """Classify the architecture with deterministic heuristics.

    Priority order: CI/CD pipeline → event-driven (queue) → microservices
    (>=3 services exposing ingress) → monolithic (1 service + a database) →
    generic ``service-oriented``.
    """
    services = [s for s in program.statements if isinstance(s, n.ServiceDef)]
    has_pipeline = any(isinstance(s, n.PipelineDef) for s in program.statements)
    has_queue = any(isinstance(s, n.QueueDef) for s in program.statements)
    has_db = any(isinstance(s, n.DatabaseDef) for s in program.statements)
    services_with_ingress = sum(1 for s in services if s.ingress is not None)

    if has_pipeline:
        return "CI/CD-first"
    if has_queue:
        return "event-driven"
    if services_with_ingress >= 3:
        return "microservices"
    if len(services) == 1 and has_db:
        return "monolithic"
    return "service-oriented"


def _base_image_name(image: Optional[str]) -> str:
    """Extract the human-meaningful image name (registry/tag stripped)."""
    if not image:
        return ""
    name = image.rsplit("/", 1)[-1]
    return name.split(":", 1)[0].split("@", 1)[0]


def _tech_stack(program: n.Program) -> List[str]:
    """Ordered, de-duplicated list of detected technologies."""
    seen: List[str] = []

    def _add(value: str) -> None:
        if value and value not in seen:
            seen.append(value)

    for stmt in program.statements:
        if isinstance(stmt, n.ServiceDef):
            _add(_base_image_name(stmt.image))
        elif isinstance(stmt, (n.DatabaseDef, n.CacheDef, n.QueueDef)):
            _add(stmt.type)
        elif isinstance(stmt, n.StorageDef):
            _add(stmt.type)
    return seen


def _display_image(svc: n.ServiceDef) -> str:
    """Image reference, or ``(build)`` for source-built services."""
    if svc.image:
        return svc.image
    if svc.build is not None:
        return "(build)"
    return "-"


def _display_port(svc: n.ServiceDef) -> str:
    """First declared port (target preferred), or ``-``."""
    if not svc.ports:
        return "-"
    p = svc.ports[0]
    value = p.target if p.target is not None else p.host
    return str(value) if value is not None else "-"


def _health_status(svc: n.ServiceDef) -> str:
    """``ok`` when any health mechanism is configured, else ``missing``."""
    if svc.health is not None or svc.probes is not None:
        return "ok"
    return "missing"


def _dep_targets(program: n.Program) -> List[str]:
    """Names of all top-level blocks that can be dependency targets."""
    targets: List[str] = []
    for stmt in program.statements:
        name = getattr(stmt, "name", None)
        if isinstance(stmt, (n.VariableDecl, n.Import)) or name is None:
            continue
        if isinstance(name, str) and name not in targets:
            targets.append(name)
    return targets


def _findings_for(finding: Any) -> Dict[str, Any]:
    """Normalize a validator-originated finding to a plain dict."""
    return {
        "code": finding.code,
        "message": finding.message,
        "hint": finding.hint,
        "file": finding.location.file if finding.location else None,
        "line": finding.location.line if finding.location else None,
    }


def _cost_categories(est: CostEstimate) -> Dict[str, float]:
    """Split the estimate into compute/storage/network/managed buckets."""
    compute = 0.0
    storage = 0.0
    managed = 0.0
    for item in est.items:
        compute += max(0.0, item.vcpu) * VCPU_MONTHLY
        compute += max(0.0, item.ram_gb) * GB_RAM_MONTHLY
        storage += max(0.0, item.storage_gb) * GB_STORAGE_MONTHLY
        if item.managed:
            managed += MANAGED_DB_MONTHLY
    return {
        "compute": round(compute, 2),
        "storage": round(storage, 2),
        "network": 0.0,  # network egress is not modeled by the estimator
        "managed": round(managed, 2),
    }


def _dependents_map(
    program: n.Program,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Return ``(dependencies, dependents)`` adjacency maps for services."""
    targets = set(_dep_targets(program))
    dependencies: Dict[str, List[str]] = {}
    dependents: Dict[str, List[str]] = {}
    for stmt in program.statements:
        if not isinstance(stmt, n.ServiceDef):
            continue
        deps = [d for d in stmt.dependencies if d in targets]
        dependencies[stmt.name] = deps
        for d in deps:
            dependents.setdefault(d, []).append(stmt.name)
    return dependencies, dependents


def _failure_blast(target: str, dependents: Dict[str, List[str]]) -> List[str]:
    """Transitively affected services when ``target`` goes down (BFS)."""
    affected: List[str] = []
    queue = list(dependents.get(target, []))
    while queue:
        name = queue.pop(0)
        if name in affected or name == target:
            continue
        affected.append(name)
        queue.extend(dependents.get(name, []))
    return affected


def _single_points_of_failure(
    program: n.Program, dependents: Dict[str, List[str]]
) -> List[Dict[str, Any]]:
    """Services with ``replicas <= 1`` that have >= 2 dependents."""
    spofs: List[Dict[str, Any]] = []
    for stmt in program.statements:
        if not isinstance(stmt, n.ServiceDef):
            continue
        count = len(dependents.get(stmt.name, []))
        if stmt.replicas <= 1 and count >= 2:
            spofs.append(
                {
                    "name": stmt.name,
                    "replicas": stmt.replicas,
                    "dependents": count,
                }
            )
    return spofs


def _scale_what_if(
    program: n.Program, svc: n.ServiceDef, findings_before: List[ReliabilityFinding]
) -> Dict[str, Any]:
    """Simulate doubling the replicas of ``svc``.

    The modified program is re-analyzed with the *real* cost estimator and
    reliability checker, so the deltas are computed, not guessed.
    """
    current = svc.replicas if svc.replicas > 0 else 1
    new_replicas = current * 2
    scaled = replace(svc, replicas=new_replicas)
    statements = tuple(scaled if s is svc else s for s in program.statements)
    scaled_program = replace(program, statements=statements)

    cost_before = estimate_cost(program).total_monthly_usd
    cost_after = estimate_cost(scaled_program).total_monthly_usd
    findings_after = ReliabilityChecker().check(scaled_program)
    return {
        "name": svc.name,
        "current_replicas": current,
        "new_replicas": new_replicas,
        "cost_delta_usd": round(cost_after - cost_before, 2),
        "reliability_delta": reliability_score(findings_after)
        - reliability_score(findings_before),
    }


@dataclass
class ServiceInsight:
    """One row of the services table."""

    name: str
    image: str
    replicas: int
    port: str
    monthly_usd: float
    health: str
    security_grade: str
    reliability_grade: str


@dataclass
class ExplainData:
    """Complete, renderer-agnostic insight snapshot of one program."""

    project: str
    checksum: str
    arch_type: str
    tech_stack: List[str]
    counts: Dict[str, int]
    total_dependencies: int
    top_costs: List[Dict[str, Any]]
    services: List[ServiceInsight] = field(default_factory=list)
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    dependents: Dict[str, List[str]] = field(default_factory=dict)
    spofs: List[Dict[str, Any]] = field(default_factory=list)
    cost_total_usd: float = 0.0
    cost_items: List[Dict[str, Any]] = field(default_factory=list)
    cost_categories: Dict[str, float] = field(default_factory=dict)
    security: List[Dict[str, Any]] = field(default_factory=list)
    reliability: List[Dict[str, Any]] = field(default_factory=list)
    whatif_failure: List[Dict[str, Any]] = field(default_factory=list)
    whatif_scale: List[Dict[str, Any]] = field(default_factory=list)
    validation_errors: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def summary_sentences(self) -> List[str]:
        """3–5 deterministic natural-language summary sentences.

        Generated purely from templates — no LLM at runtime.
        """
        tech = ", ".join(self.tech_stack) if self.tech_stack else "custom images"
        n_services = self.counts.get("services", 0)
        svc_word = "service" if n_services == 1 else "services"
        risks: List[str] = [item["code"] for item in self.security[:2]]
        risks += [item["code"] for item in self.reliability[: 3 - len(risks)]]
        risk_text = ", ".join(risks) if risks else "none detected"
        sentences = [
            f"This is a {self.arch_type} architecture with "
            f"{n_services} {svc_word}.",
            f"Primary tech: {tech}.",
            f"Est. monthly cost: ${self.cost_total_usd:.2f}.",
            f"Key risks: {risk_text}.",
        ]
        if self.spofs:
            names = ", ".join(s["name"] for s in self.spofs)
            sentences.append(f"Single points of failure: {names}.")
        return sentences


def source_checksum(source: str) -> str:
    """Short deterministic SHA-256 fingerprint of the source text."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]


def collect_explain_data(
    program: n.Program, *, source: str, project: str
) -> ExplainData:
    """Build the insight snapshot from the existing analyzers.

    ``project`` is a display name (usually derived from the file name).
    The semantic validator always runs; when it reports errors they are
    surfaced in :attr:`ExplainData.validation_errors` and the rest of the
    report is still produced (best-effort insight over a broken file).
    """
    validation = SemanticValidator().validate(program)
    sec_findings: List[Any] = SecurityChecker().check(program)
    rel_findings: List[ReliabilityFinding] = ReliabilityChecker().check(program)
    est = estimate_cost(program)
    cost_items = est.to_dict()["breakdown"]

    counts = {
        "services": 0,
        "databases": 0,
        "caches": 0,
        "queues": 0,
        "storages": 0,
        "pipelines": 0,
        "network_policies": 0,
        "secret_stores": 0,
    }
    for stmt in program.statements:
        if isinstance(stmt, n.ServiceDef):
            counts["services"] += 1
        elif isinstance(stmt, n.DatabaseDef):
            counts["databases"] += 1
        elif isinstance(stmt, n.CacheDef):
            counts["caches"] += 1
        elif isinstance(stmt, n.QueueDef):
            counts["queues"] += 1
        elif isinstance(stmt, n.StorageDef):
            counts["storages"] += 1
        elif isinstance(stmt, n.PipelineDef):
            counts["pipelines"] += 1
        elif isinstance(stmt, n.NetworkPolicyDef):
            counts["network_policies"] += 1
        elif isinstance(stmt, n.SecretStoreDef):
            counts["secret_stores"] += 1

    dependencies, dependents = _dependents_map(program)
    total_dependencies = sum(len(v) for v in dependencies.values())

    # Per-service insight rows. Security/reliability sub-grades are computed
    # by re-running the checkers on a single-statement program so the grade
    # reflects exactly the findings raised for that service.
    cost_by_name = {i["name"]: i["monthly_usd"] for i in cost_items}
    services: List[ServiceInsight] = []
    for stmt in program.statements:
        if not isinstance(stmt, n.ServiceDef):
            continue
        solo = n.Program(statements=(stmt,))
        sec_count = len(SecurityChecker().check(solo))
        rel_count = len(ReliabilityChecker().check(solo))
        services.append(
            ServiceInsight(
                name=stmt.name,
                image=_display_image(stmt),
                replicas=stmt.replicas,
                port=_display_port(stmt),
                monthly_usd=cost_by_name.get(stmt.name, 0.0),
                health=_health_status(stmt),
                security_grade=_grade(sec_count),
                reliability_grade=_grade(rel_count),
            )
        )

    top_costs = sorted(cost_items, key=lambda i: -i["monthly_usd"])[:_TOP_COSTS]

    whatif_failure = [
        {
            "target": svc.name,
            "affected": _failure_blast(svc.name, dependents),
        }
        for svc in program.statements
        if isinstance(svc, n.ServiceDef)
    ]
    whatif_scale = [
        _scale_what_if(program, svc, rel_findings)
        for svc in program.statements
        if isinstance(svc, n.ServiceDef)
    ]

    validation_errors: List[Dict[str, Any]] = [
        _findings_for(e) for e in validation.errors
    ]

    return ExplainData(
        project=project,
        checksum=source_checksum(source),
        arch_type=infer_arch_type(program),
        tech_stack=_tech_stack(program),
        counts=counts,
        total_dependencies=total_dependencies,
        top_costs=top_costs,
        services=services,
        dependencies=dependencies,
        dependents=dependents,
        spofs=_single_points_of_failure(program, dependents),
        cost_total_usd=est.total_monthly_usd,
        cost_items=cost_items,
        cost_categories=_cost_categories(est),
        security=[_findings_for(f) for f in sec_findings],
        reliability=[
            {
                **_findings_for(f),
                "impact": REL_IMPACT.get(f.code, "medium"),
            }
            for f in rel_findings
        ],
        whatif_failure=whatif_failure,
        whatif_scale=whatif_scale,
        validation_errors=validation_errors,
    )


__all__ = [
    "SECTION_IDS",
    "SECTION_TITLES",
    "REL_IMPACT",
    "ExplainData",
    "ServiceInsight",
    "ValidationError",
    "ValidationWarning",
    "collect_explain_data",
    "infer_arch_type",
    "reliability_score",
    "source_checksum",
]
