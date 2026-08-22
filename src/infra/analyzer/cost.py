"""Static cloud cost estimation for an Infra program.

Walks the AST and estimates a monthly cost in USD using simple, documented
per-unit cloud rates. This is a rough order-of-magnitude estimate intended for
planning and CI gates — not a billing-grade quote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from infra.parser import ast_nodes as n

#: Base monthly rates (USD) used for the estimate.
VCPU_MONTHLY = 30.0  # 1 vCPU
GB_RAM_MONTHLY = 4.0  # 1 GB RAM
GB_STORAGE_MONTHLY = 0.10  # 1 GB persistent storage
MANAGED_DB_MONTHLY = 25.0  # managed database base fee
CACHE_NODE_MONTHLY = 20.0  # managed cache base fee

#: Memory unit -> bytes multiplier (for ResourceValue units like Mi/Gi).
_BYTE_FACTORS = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
}


@dataclass
class CostItem:
    """Cost contribution of a single resource."""

    name: str
    kind: str
    vcpu: float = 0.0
    ram_gb: float = 0.0
    storage_gb: float = 0.0
    managed: bool = False

    @property
    def monthly_usd(self) -> float:
        # Defensive: never let a (possibly negative) component shrink the bill
        # below the managed base fee.
        total = (
            max(0.0, self.vcpu) * VCPU_MONTHLY
            + max(0.0, self.ram_gb) * GB_RAM_MONTHLY
            + max(0.0, self.storage_gb) * GB_STORAGE_MONTHLY
        )
        if self.managed:
            total += MANAGED_DB_MONTHLY
        return round(max(0.0, total), 2)


@dataclass
class CostEstimate:
    """Full cost estimate: per-resource breakdown + monthly total."""

    items: List[CostItem] = field(default_factory=list)

    @property
    def total_monthly_usd(self) -> float:
        return round(sum(i.monthly_usd for i in self.items), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_monthly_usd": self.total_monthly_usd,
            "breakdown": [
                {
                    "name": i.name,
                    "kind": i.kind,
                    "vcpu": i.vcpu,
                    "ram_gb": i.ram_gb,
                    "storage_gb": i.storage_gb,
                    "managed": i.managed,
                    "monthly_usd": i.monthly_usd,
                }
                for i in self.items
            ],
        }


def _resource_value_to_bytes(rv: n.ResourceValue) -> int:
    """Return the ResourceValue in bytes (memory) or cpu count.

    Values are clamped to a non-negative floor so a malformed/negative quantity
    can never produce a negative cost or shrink a bill.
    """
    value = max(0.0, float(rv.value))
    if rv.unit in _BYTE_FACTORS:
        return int(value * _BYTE_FACTORS[rv.unit])
    return int(value)


def _resource_map_gb(rm: n.ResourceMap) -> float:
    """Estimate the RAM (GB) implied by a ResourceMap (requests/limits)."""
    if rm is None or rm.memory is None:
        return 0.0
    return _resource_value_to_bytes(rm.memory) / (1024**3)


def _cpu_value(rv: n.ResourceValue) -> float:
    """Estimate vCPU from a cpu ResourceValue (cpus or milli-cpus)."""
    value = max(0.0, float(rv.value))
    if rv.unit == "m":
        return value / 1000.0
    if rv.unit == "cores":
        return value
    return value


def _service_cost(svc: n.ServiceDef) -> CostItem:
    replicas = max(1, int(svc.replicas or 1))
    vcpu = ram = 0.0
    if svc.resources:
        # use limits if present, else requests, else a 1 vCPU / 512Mi default
        for section in (svc.resources.limits, svc.resources.requests):
            if section is not None and (section.cpu or section.memory):
                if section.cpu:
                    vcpu = _cpu_value(section.cpu)
                if section.memory:
                    ram = _resource_map_gb(n.ResourceMap(memory=section.memory))
                break
        else:
            vcpu, ram = 1.0, 0.5
    else:
        vcpu, ram = 1.0, 0.5
    return CostItem(
        name=svc.name,
        kind="service",
        vcpu=round(vcpu * replicas, 2),
        ram_gb=round(ram * replicas, 2),
    )


def _database_cost(db: n.DatabaseDef) -> CostItem:
    vcpu = 2.0  # managed DB base sizing
    ram = 4.0
    storage_gb = 0.0
    if db.storage:
        storage_gb = _resource_value_to_bytes(db.storage) / (1024**3)
    elif db.size:
        storage_gb = _resource_value_to_bytes(db.size) / (1024**3)
    else:
        storage_gb = 100.0  # default managed DB volume
    return CostItem(
        name=db.name,
        kind="database",
        vcpu=vcpu,
        ram_gb=ram,
        storage_gb=round(storage_gb, 2),
        managed=True,
    )


def _cache_cost(cache: n.CacheDef) -> CostItem:
    return CostItem(
        name=cache.name, kind="cache", vcpu=1.0, ram_gb=2.0, managed=True
    )


def _storage_cost(storage: n.StorageDef) -> CostItem:
    size_gb = 100.0
    if storage.size:
        size_gb = _resource_value_to_bytes(storage.size) / (1024**3)
    return CostItem(
        name=storage.name, kind="storage", storage_gb=round(size_gb, 2)
    )


def estimate_cost(program: n.Program) -> CostEstimate:
    """Estimate the monthly cost of a parsed Infra program."""
    items: List[CostItem] = []
    for stmt in program.statements:
        if isinstance(stmt, n.ServiceDef):
            items.append(_service_cost(stmt))
        elif isinstance(stmt, n.DatabaseDef):
            items.append(_database_cost(stmt))
        elif isinstance(stmt, n.CacheDef):
            items.append(_cache_cost(stmt))
        elif isinstance(stmt, n.StorageDef):
            items.append(_storage_cost(stmt))
    return CostEstimate(items=items)
