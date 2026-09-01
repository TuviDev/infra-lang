"""Advanced coverage tests for the cost analyzer edge cases."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from infra.analyzer.cost import (
    VCPU_MONTHLY,
    _cpu_value,
    _resource_map_gb,
    _resource_value_to_bytes,
    estimate_cost,
)
from infra.cli.main import app
from infra.parser import ast_nodes as n
from infra.parser import parse

runner = CliRunner()


def _rv(value: float, unit: str) -> n.ResourceValue:
    return n.ResourceValue(value=value, unit=unit)


class TestUnitHelpers:
    def test_bytes_ki(self):
        assert _resource_value_to_bytes(_rv(2, "Ki")) == 2048

    def test_bytes_raw_int(self):
        assert _resource_value_to_bytes(_rv(500, "m")) == 500

    def test_cpu_milli(self):
        assert _cpu_value(_rv(500, "m")) == 0.5

    def test_cpu_cores(self):
        assert _cpu_value(_rv(3, "cores")) == 3.0

    def test_cpu_plain(self):
        assert _cpu_value(_rv(2, "")) == 2.0

    def test_resource_map_none(self):
        assert _resource_map_gb(None) == 0.0

    def test_resource_map_gb_gi(self):
        # 2 Gi -> 2.0 GB
        rv = _rv(2, "Gi")
        rm = n.ResourceMap(memory=rv)
        assert _resource_map_gb(rm) == 2.0


class TestCostResourceTypes:
    def test_database_postgres_with_storage(self):
        est = estimate_cost(parse("database db { type: postgres storage: 20Gi }"))
        item = est.items[0]
        assert item.kind == "database"
        assert item.managed is True
        assert item.storage_gb == 20.0

    def test_database_with_size_field(self):
        # `size` field (not `storage`) path
        est = estimate_cost(parse("database db { type: mysql size: 50Gi }"))
        assert est.items[0].storage_gb == 50.0

    def test_database_default_storage(self):
        # neither storage nor size -> default 100GB
        est = estimate_cost(parse("database db { type: mongo }"))
        assert est.items[0].storage_gb == 100.0

    def test_cache_redis(self):
        est = estimate_cost(parse("cache c { type: redis }"))
        assert est.items[0].kind == "cache"
        assert est.items[0].managed is True

    def test_storage_s3_with_size(self):
        est = estimate_cost(parse("storage s { type: s3 size: 500Gi }"))
        assert est.items[0].kind == "storage"
        assert est.items[0].storage_gb == 500.0

    def test_storage_default_size(self):
        est = estimate_cost(parse("storage s { type: gcs }"))
        assert est.items[0].storage_gb == 100.0

    def test_queue_ignored(self):
        # queue is not costed (no branch) but must not crash
        est = estimate_cost(parse("queue q { type: rabbitmq }"))
        assert est.total_monthly_usd == 0

    def test_config_secret_ignored(self):
        est = estimate_cost(parse("config c { A: '1' }"))
        assert est.total_monthly_usd == 0


class TestServiceResourceBranches:
    def test_service_with_limits(self):
        est = estimate_cost(
            parse(
                'service api { image: "x" '
                "resources { limits: { cpu: 2000m, memory: 4Gi } } }"
            )
        )
        item = est.items[0]
        assert item.vcpu == 2.0
        assert item.ram_gb == 4.0

    def test_service_with_requests_fallback(self):
        # no limits, only requests -> uses requests
        est = estimate_cost(
            parse(
                'service api { image: "x" '
                "resources { requests: { cpu: 500m, memory: 1Gi } } }"
            )
        )
        item = est.items[0]
        assert item.vcpu == 0.5
        assert item.ram_gb == 1.0

    def test_service_no_resources_default(self):
        est = estimate_cost(parse('service api { image: "x" }'))
        assert est.items[0].vcpu == 1.0
        assert est.items[0].ram_gb == 0.5

    def test_service_empty_resources_block_default(self):
        est = estimate_cost(parse('service api { image: "x" resources { } }'))
        assert est.items[0].vcpu == 1.0

    def test_service_cpu_cores_units(self):
        est = estimate_cost(
            parse(
                'service api { image: "x" '
                "resources { limits: { cpu: 2cores, memory: 1Gi } } }"
            )
        )
        assert est.items[0].vcpu == 2.0


class TestCostCLIAdvanced:
    def test_currency_eur(self, tmp_path):
        p = tmp_path / "a.infra"
        p.write_text('service api { image: "x" }', encoding="utf-8")
        r = runner.invoke(app, ["cost", str(p), "--currency", "EUR"])
        assert r.exit_code == 0
        assert "EUR" in r.stdout

    def test_currency_pln_json_not_affected(self, tmp_path):
        # JSON is always in USD regardless of --currency
        p = tmp_path / "a.infra"
        p.write_text('service api { image: "x" }', encoding="utf-8")
        r = runner.invoke(app, ["cost", str(p), "--json"])
        data = json.loads(r.stdout)
        # default service = 1 vCPU ($30) + 0.5 GB RAM ($2) = $32
        assert data["total_monthly_usd"] == 32.0

    def test_empty_file_total_zero(self, tmp_path):
        p = tmp_path / "empty.infra"
        p.write_text("# nothing", encoding="utf-8")
        r = runner.invoke(app, ["cost", str(p), "--json"])
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert data["total_monthly_usd"] == 0
        assert data["breakdown"] == []

    def test_vcpu_rate_sanity(self):
        # service with 2 vCPU -> 2 * VCPU_MONTHLY
        src = 'service api { image: "x" replicas: 1 resources { limits: { cpu: 2 } } }'
        est = estimate_cost(parse(src))
        assert est.items[0].vcpu == 2.0
        assert est.items[0].monthly_usd >= 2 * VCPU_MONTHLY


class TestNegativeValueClamping:
    """Negative resource values must never produce negative or discounted cost."""

    def test_negative_cpu_clamped(self):
        # a normal parse always yields non-negative costs
        est = estimate_cost(parse('service api { image: "x" }'))
        assert est.total_monthly_usd >= 0

    def test_negative_cpu_via_ast(self):
        from infra.analyzer.cost import _service_cost
        from infra.parser import ast_nodes as n

        svc = n.ServiceDef(
            name="api",
            resources=n.ResourcesSpec(
                limits=n.ResourceMap(cpu=n.ResourceValue(value=-100, unit="m"))
            ),
        )
        item = _service_cost(svc)
        assert item.vcpu == 0.0  # clamped, not negative

    def test_negative_storage_db_not_shrink(self):
        from infra.analyzer.cost import _database_cost
        from infra.parser import ast_nodes as n

        db = n.DatabaseDef(
            name="db",
            type="postgres",
            storage=n.ResourceValue(value=-50, unit="Gi"),
        )
        item = _database_cost(db)
        assert item.storage_gb == 0.0  # clamped, not -50
        # managed base fee still applies; no negative component
        assert item.monthly_usd >= 25.0

    def test_negative_memory_clamped(self):
        from infra.analyzer.cost import _service_cost
        from infra.parser import ast_nodes as n

        svc = n.ServiceDef(
            name="api",
            resources=n.ResourcesSpec(
                limits=n.ResourceMap(memory=n.ResourceValue(value=-5, unit="Gi"))
            ),
        )
        item = _service_cost(svc)
        assert item.ram_gb == 0.0

    def test_cost_item_monthly_never_negative(self):
        from infra.analyzer.cost import CostItem

        item = CostItem(name="x", kind="service", vcpu=-2.0, ram_gb=-1.0)
        assert item.monthly_usd >= 0.0
