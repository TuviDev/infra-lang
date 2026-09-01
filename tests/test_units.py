"""Unit disambiguation tests.

Verify that ``m`` is milli (CPU) in resource contexts while minutes are
written ``min``, and that time/resource units map to the right AST nodes.
"""

from __future__ import annotations

import pytest

from infra import parse
from infra.parser.ast_nodes import (
    Duration,
    ResourceValue,
    VariableDecl,
)


def _user_statements(program):
    return [
        s
        for s in program.statements
        if getattr(getattr(s, "location", None), "file", "") != "<prelude>"
    ]


def _first_user(program):
    return _user_statements(program)[0]


def _var_value(src: str, name: str):
    program = parse(src)
    for s in _user_statements(program):
        if isinstance(s, VariableDecl) and s.name == name:
            return s.value
    return None


class TestTimeUnits:
    def test_seconds(self):
        v = _var_value("let t = 30s", "t")
        assert isinstance(v, Duration)
        assert v.to_seconds() == 30.0

    def test_minutes_with_min(self):
        v = _var_value("let t = 5min", "t")
        assert isinstance(v, Duration)
        assert v.unit == "min"
        assert v.to_seconds() == 300.0

    def test_hours(self):
        v = _var_value("let t = 2h", "t")
        assert isinstance(v, Duration)
        assert v.to_seconds() == 7200.0

    def test_days(self):
        v = _var_value("let t = 7d", "t")
        assert isinstance(v, Duration)
        assert v.to_seconds() == 604800.0

    def test_milliseconds(self):
        v = _var_value("let t = 500ms", "t")
        assert isinstance(v, Duration)
        assert v.to_seconds() == 0.5

    def test_weeks(self):
        v = _var_value("let t = 1w", "t")
        assert isinstance(v, Duration)
        assert v.to_seconds() == 604800.0

    @pytest.mark.parametrize(
        "unit,secs",
        [
            ("ms", 0.001),
            ("s", 1.0),
            ("min", 60.0),
            ("h", 3600.0),
            ("d", 86400.0),
            ("w", 604800.0),
        ],
    )
    def test_all_time_units(self, unit, secs):
        v = _var_value(f"let t = 1{unit}", "t")
        assert isinstance(v, Duration)
        assert v.to_seconds() == secs


class TestResourceUnits:
    def test_milli_cores_in_cpu(self):
        program = parse('service a { image: "x" resources { cpu: 500m } }')
        svc = _first_user(program)
        cpu = svc.resources.requests.cpu
        assert isinstance(cpu, ResourceValue)
        assert cpu.value == 500
        assert cpu.unit == "m"

    def test_cores_unit(self):
        program = parse('service a { image: "x" resources { cpu: 2cores } }')
        svc = _first_user(program)
        assert svc.resources.requests.cpu.unit == "cores"

    def test_mebibytes(self):
        program = parse('service a { image: "x" resources { memory: 128Mi } }')
        svc = _first_user(program)
        mem = svc.resources.requests.memory
        assert mem.value == 128 and mem.unit == "Mi"

    def test_gibibytes_storage(self):
        program = parse("database db { type: postgres storage: 10Gi }")
        db = _first_user(program)
        storage = db.storage or db.size
        assert storage.value == 10 and storage.unit == "Gi"

    @pytest.mark.parametrize(
        "unit", ["Ki", "Mi", "Gi", "Ti", "MB", "GB", "TB", "m", "cores"]
    )
    def test_all_resource_units_parse(self, unit):
        program = parse(f'service a {{ image: "x" resources {{ memory: 1{unit} }} }}')
        svc = _first_user(program)
        assert svc.resources.requests.memory is not None

    def test_to_kubernetes_formatting(self):
        assert ResourceValue(500, "m").to_kubernetes() == "500m"
        assert ResourceValue(128, "Mi").to_kubernetes() == "128Mi"
        assert ResourceValue(2, "Gi").to_kubernetes() == "2Gi"
