"""Reliability (chaos-inspired) lint rules.

These are *facts* about a definition (no invented probability numbers):
missing health checks, no memory limits, even HA replica counts, missing
backups, deep dependency chains, etc. They are reported as warnings and do not
block compilation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from infra.parser import ast_nodes as n


@dataclass
class ReliabilityFinding:
    code: str
    message: str
    location: Optional[n.SourceLocation] = None
    hint: Optional[str] = None


def _replicas(svc: n.ServiceDef) -> int:
    r = svc.replicas
    if isinstance(r, int):
        return r
    if isinstance(r, n.Literal):
        try:
            return int(r.value)
        except (TypeError, ValueError):
            return 1
    return 1


def _dep_names(svc: n.ServiceDef) -> list[str]:
    return list(svc.depends)


class ReliabilityChecker:
    def check(self, program: n.Program) -> list[ReliabilityFinding]:
        findings = []
        services = [s for s in program.statements if isinstance(s, n.ServiceDef)]
        databases = [s for s in program.statements if isinstance(s, n.DatabaseDef)]
        caches = [s for s in program.statements if isinstance(s, n.CacheDef)]

        depends_on = {s.name: _dep_names(s) for s in services}

        for svc in services:
            findings += self._rel001_thundering_herd(svc)
            findings += self._rel003_no_memory_limit(svc)
            findings += self._rel004_no_health(svc)
            findings += self._rel005_deep_dependency(svc, depends_on)
            findings += self._rel007_single_replica_depended(svc, services)
            findings += self._rel009_no_graceful_shutdown(svc)
            findings += self._rel011_autoscale_without_limits(svc)
            findings += self._rel012_autoscale_with_replicas(svc)
        for db in databases:
            findings += self._rel002_even_replicas_ha(db)
            findings += self._rel006_no_backup(db)
            findings += self._rel013_database_no_resources(db)
        for queue in program.statements:
            if isinstance(queue, n.QueueDef):
                findings += self._rel014_kafka_single_replica(queue)
        for cache in caches:
            findings += self._rel008_cache_no_persistence(cache, services)
        return findings

    def _rel001_thundering_herd(self, svc: n.ServiceDef) -> list[ReliabilityFinding]:
        if _replicas(svc) < 5:
            return []
        has_startup = svc.probes is not None and svc.probes.startup is not None
        if has_startup:
            return []
        return [
            ReliabilityFinding(
                code="REL001",
                message=(
                    f"Service '{svc.name}' has {_replicas(svc)} replicas but no startup "  # noqa: E501
                    "probe. Simultaneous restart can cause a thundering herd on "
                    "downstream services."
                ),
                location=svc.location,
                hint='Add probes { startup { http: "/ready" } }',
            )
        ]

    def _rel002_even_replicas_ha(self, db: n.DatabaseDef) -> list[ReliabilityFinding]:
        if not db.ha:
            return []
        replicas = db.replicas if isinstance(db.replicas, int) else 1
        if replicas % 2 != 0:
            return []
        return [
            ReliabilityFinding(
                code="REL002",
                message=(
                    f"Database '{db.name}' has {replicas} replicas (even) with HA "
                    "enabled. Even replica counts risk split-vote during network "
                    "partitions."
                ),
                location=db.location,
                hint=f"Change replicas: {replicas} to replicas: {replicas + 1}",
            )
        ]

    def _rel003_no_memory_limit(self, svc: n.ServiceDef) -> list[ReliabilityFinding]:
        has_limit = False
        if svc.resources is not None:
            if (
                svc.resources.limits is not None
                and svc.resources.limits.memory is not None
            ):
                has_limit = True
            elif (
                svc.resources.requests is not None
                and svc.resources.requests.memory is not None
            ):
                has_limit = True
        if has_limit:
            return []
        return [
            ReliabilityFinding(
                code="REL003",
                message=(
                    f"Service '{svc.name}' has no memory limit. A memory leak can trigger "  # noqa: E501
                    "OOM on the node, affecting other services."
                ),
                location=svc.location,
                hint="Add resources { limits { memory: 512Mi } }",
            )
        ]

    def _rel004_no_health(self, svc: n.ServiceDef) -> list[ReliabilityFinding]:
        if svc.health is not None or svc.probes is not None:
            return []
        return [
            ReliabilityFinding(
                code="REL004",
                message=(
                    f"Service '{svc.name}' has no health checks. Kubernetes cannot "
                    "determine when the pod is ready to receive traffic."
                ),
                location=svc.location,
                hint='Add health http("/health")',
            )
        ]

    def _rel005_deep_dependency(
        self, svc: n.ServiceDef, depends_on: dict[str, list[str]]
    ) -> list[ReliabilityFinding]:
        depth = self._bfs_depth(svc.name, depends_on)
        if depth < 4:
            return []
        return [
            ReliabilityFinding(
                code="REL005",
                message=(
                    f"Service '{svc.name}' has a dependency chain depth of {depth}. "
                    "Deep chains amplify cascade failures."
                ),
                location=svc.location,
                hint="Consider timeouts/retries to limit blast radius.",
            )
        ]

    def _bfs_depth(self, start: str, depends_on: dict[str, list[str]]) -> int:
        visited = {start}
        q = deque([(start, 0)])
        max_depth = 0
        while q:
            node, depth = q.popleft()
            max_depth = max(max_depth, depth)
            for dep in depends_on.get(node, []):
                if dep not in visited:
                    visited.add(dep)
                    q.append((dep, depth + 1))
        return max_depth

    def _rel006_no_backup(self, db: n.DatabaseDef) -> list[ReliabilityFinding]:
        if db.backup is not None and db.backup.enabled:
            return []
        return [
            ReliabilityFinding(
                code="REL006",
                message=f"Database '{db.name}' has no backup configured. Data loss is permanent.",  # noqa: E501
                location=db.location,
                hint='Add backup { enabled: true schedule: "0 2 * * *" retention: 30d }',  # noqa: E501
            )
        ]

    def _rel007_single_replica_depended(
        self, svc: n.ServiceDef, services: list[n.ServiceDef]
    ) -> list[ReliabilityFinding]:
        if _replicas(svc) != 1:
            return []
        dependents = [s.name for s in services if svc.name in _dep_names(s)]
        if not dependents:
            return []
        return [
            ReliabilityFinding(
                code="REL007",
                message=(
                    f"Service '{svc.name}' runs with 1 replica but "
                    f"{len(dependents)} service(s) depend on it: {', '.join(dependents)}. "  # noqa: E501
                    "Single point of failure."
                ),
                location=svc.location,
                hint="Consider replicas: 2 for high availability.",
            )
        ]

    def _rel008_cache_no_persistence(
        self, cache: n.CacheDef, services: list[n.ServiceDef]
    ) -> list[ReliabilityFinding]:
        if cache.type != "redis" or cache.persistence:
            return []
        dependents = [s.name for s in services if cache.name in _dep_names(s)]
        if not dependents:
            return []
        return [
            ReliabilityFinding(
                code="REL008",
                message=(
                    f"Redis cache '{cache.name}' has persistence disabled. All cached "
                    "data is lost on restart."
                ),
                location=cache.location,
                hint="Add persistence: true if data must survive restarts.",
            )
        ]

    def _rel009_no_graceful_shutdown(
        self, svc: n.ServiceDef
    ) -> list[ReliabilityFinding]:
        if _replicas(svc) <= 1:
            return []
        if svc.lifecycle is not None and svc.lifecycle.pre_stop is not None:
            return []
        return [
            ReliabilityFinding(
                code="REL009",
                message=(
                    f"Service '{svc.name}' has no preStop lifecycle hook. Active "
                    "connections may be dropped during rolling updates."
                ),
                location=svc.location,
                hint='Add lifecycle { preStop { exec: ["sleep", "5"] } }',
            )
        ]

    def _rel011_autoscale_without_limits(
        self, svc: n.ServiceDef
    ) -> list[ReliabilityFinding]:
        """REL011: autoscale without CPU limits prevents HPA utilization calc."""
        if svc.autoscale is None:
            return []
        has_limits = (
            svc.resources is not None
            and svc.resources.limits is not None
            and (svc.resources.limits.cpu is not None or svc.resources.limits.memory is not None)  # noqa: E501
        )
        if has_limits:
            return []
        return [
            ReliabilityFinding(
                code="REL011",
                message=(
                    f"Service '{svc.name}' has autoscale but no CPU limits. "
                    "HPA cannot calculate utilization without limits."
                ),
                location=svc.location,
                hint="Add resources { limits { cpu: 500m } }",
            )
        ]

    def _rel012_autoscale_with_replicas(
        self, svc: n.ServiceDef
    ) -> list[ReliabilityFinding]:
        """REL012: autoscale and fixed replicas conflict."""
        if svc.autoscale is None:
            return []
        if svc.replicas is None or _replicas(svc) == 1:
            return []
        return [
            ReliabilityFinding(
                code="REL012",
                message=(
                    f"Service '{svc.name}' has autoscale and a fixed "
                    f"replicas: {_replicas(svc)}. Replicas is ignored when "
                    "autoscale is set."
                ),
                location=svc.location,
                hint=f"Remove replicas: {_replicas(svc)} when using autoscale.",
            )
        ]

    def _rel013_database_no_resources(
        self, db: n.DatabaseDef
    ) -> list[ReliabilityFinding]:
        """REL013: database without resource allocation."""
        if db.size is not None or db.storage is not None:
            return []
        return [
            ReliabilityFinding(
                code="REL013",
                message=(
                    f"Database '{db.name}' has no resource limits. "
                    "Consider allocating storage and sizing."
                ),
                location=db.location,
                hint="Add storage: 20Gi (or a size) to the database.",
            )
        ]

    def _rel014_kafka_single_replica(
        self, queue: n.QueueDef
    ) -> list[ReliabilityFinding]:
        """REL014: Kafka with 1 replica has no fault tolerance."""
        if queue.type != "kafka":
            return []
        if _replicas_int(queue.replicas) > 1:
            return []
        return [
            ReliabilityFinding(
                code="REL014",
                message=(
                    f"Kafka queue '{queue.name}' has 1 replica, so it has "
                    "no fault tolerance."
                ),
                location=queue.location,
                hint="Use replicas: 3 for production Kafka.",
            )
        ]


def _replicas_int(value) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, n.Literal):
        try:
            return int(value.value)
        except (TypeError, ValueError):
            return 1
    return 1
