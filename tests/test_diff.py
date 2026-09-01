"""Infra diff engine tests."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from infra import parse
from infra.cli.main import app
from infra.diff.engine import InfraDiff

runner = CliRunner()


def diff(src1: str, src2: str):
    return InfraDiff().diff(parse(src1), parse(src2))


class TestNoDiff:
    def test_identical(self):
        src = 'service api { image: "nginx:1.0" }'
        r = diff(src, src)
        assert not r.has_changes
        assert "api" in r.unchanged

    def test_empty_to_empty(self):
        assert not diff("", "").has_changes


class TestAdded:
    def test_added_service(self):
        r = diff(
            'service api { image: "nginx:1.0" }',
            'service api { image: "nginx:1.0" }\nservice worker { image: "redis:7" }',
        )
        assert any(i.name == "worker" and i.kind == "service" for i in r.added)

    def test_empty_to_populated(self):
        r = diff("", 'service a { image: "nginx:1.0" }\ndatabase b { type: postgres }')
        assert len(r.added) == 2
        assert not r.removed


class TestRemoved:
    def test_removed_service(self):
        r = diff(
            'service api { image: "nginx:1.0" }\nservice old { image: "legacy:1.0" }',
            'service api { image: "nginx:1.0" }',
        )
        assert any(i.name == "old" for i in r.removed)


class TestChanged:
    def test_changed_replicas(self):
        r = diff(
            'service api { image:"nginx:1.0" replicas:2 }',
            'service api { image:"nginx:1.0" replicas:5 }',
        )
        c = next(c for c in r.changed if c.name == "api")
        assert any("replica" in ch.field_path.lower() for ch in c.changes)

    def test_changed_image(self):
        r = diff(
            'service api { image: "nginx:1.0" }', 'service api { image: "nginx:2.0" }'
        )
        c = next(c for c in r.changed if c.name == "api")
        img = next(ch for ch in c.changes if "image" in ch.field_path)
        assert "1.0" in str(img.before) and "2.0" in str(img.after)

    def test_staging_vs_production(self):
        staging = (
            'service api { image:"myapp:v1" replicas:2 }\ndatabase db { type:postgres '
            'storage:10Gi }'
        )
        prod = (
            'service api { image:"myapp:v1" replicas:5 }\ndatabase db { type:postgres '
            'storage:100Gi }'
        )
        r = diff(staging, prod)
        assert not r.added and not r.removed
        assert len(r.changed) == 2


class TestFormatting:
    def test_format_text(self):
        r = diff(
            'service api { image:"nginx:1.0" }', 'service api { image:"nginx:2.0" }'
        )
        assert "api" in r.format(color=False)

    def test_format_json(self):
        r = diff(
            'service api { image:"nginx:1.0" }',
            'service api { image:"nginx:2.0" }\nservice worker { image:"redis:7" }',
        )
        data = json.loads(r.format_json())
        assert data["has_changes"] is True
        assert "added" in data and "changed" in data


class TestDiffCLI:
    def test_cli_no_changes(self, tmp_path):
        f1 = tmp_path / "a.infra"
        f2 = tmp_path / "b.infra"
        content = 'service api { image: "nginx:1.0" }'
        f1.write_text(content)
        f2.write_text(content)
        result = runner.invoke(app, ["diff", str(f1), str(f2)])
        assert result.exit_code == 0

    def test_cli_json(self, tmp_path):
        f1 = tmp_path / "a.infra"
        f2 = tmp_path / "b.infra"
        f1.write_text('service api { image:"nginx:1.0" }')
        f2.write_text('service api { image:"nginx:2.0" }')
        result = runner.invoke(app, ["diff", str(f1), str(f2), "--format", "json"])
        assert result.exit_code == 0
        assert json.loads(result.output)["has_changes"] is True


class TestDiffNodeValues:
    """Diff must surface changes across value types (resource, duration, etc.)."""

    def test_resource_value_change_detected(self):
        r = diff(
            'service s { image: "x:1" resources { requests { cpu: 100m } } }',
            'service s { image: "x:1" resources { requests { cpu: 200m } } }',
        )
        assert r.has_changes
        c = next(c for c in r.changed if c.name == "s")
        assert any("cpu" in ch.field_path for ch in c.changes)

    def test_duration_change_detected(self):
        r = diff(
            'service s { image: "x:1" health http("/") { interval: 10s } }',
            'service s { image: "x:1" health http("/") { interval: 20s } }',
        )
        assert r.has_changes
        c = next(c for c in r.changed if c.name == "s")
        assert any("interval" in ch.field_path for ch in c.changes)

    def test_nested_resources_change_detected(self):
        r = diff(
            'service s { image: "x:1" resources { limits { memory: 128Mi } } }',
            'service s { image: "x:1" resources { limits { memory: 256Mi } } }',
        )
        assert r.has_changes
        c = next(c for c in r.changed if c.name == "s")
        assert any("memory" in ch.field_path for ch in c.changes)

    def test_tuple_length_change_detected(self):
        r = diff(
            'service s { image: "x:1" replicas: 2 }',
            'service s { image: "x:1" replicas: 2 port: 80 }',
        )
        assert r.has_changes


class TestDiffSummary:
    """Diff text/JSON summary contracts (consolidated from test_diff_s10)."""

    def test_json_summary_present(self):
        r = diff(
            'service api { image: "nginx:1.0" }', 'service api { image: "nginx:2.0" }'
        )
        data = json.loads(r.format_json())
        assert "summary" in data
        assert data["summary"] == {"changed": 1, "added": 0, "removed": 0}

    def test_json_summary_counts(self):
        r = diff(
            'service api { image: "nginx:1.0" }',
            'service api { image: "nginx:2.0" }\nservice worker { image: "redis:7" }\n',
        )
        data = json.loads(r.format_json())
        assert data["summary"] == {"changed": 1, "added": 1, "removed": 0}

    def test_text_summary_line(self):
        r = diff(
            'service api { image: "nginx:1.0" }', 'service api { image: "nginx:2.0" }'
        )
        text = r.format(color=False)
        assert "SUMMARY:" in text
        assert "1 changed, 0 added, 0 removed" in text

    def test_no_differences_message(self):
        r = diff(
            'service api { image: "nginx:1.0" }', 'service api { image: "nginx:1.0" }'
        )
        assert "No differences found" in r.format(color=False)

    def test_changed_before_after_values(self):
        r = diff(
            'service api { image: "nginx:1.0" }', 'service api { image: "nginx:2.0" }'
        )
        c = next(c for c in r.changed if c.name == "api")
        img = next(ch for ch in c.changes if "image" in ch.field_path)
        assert img.before == "nginx:1.0"
        assert img.after == "nginx:2.0"

    def test_format_json_is_valid(self):
        r = diff('service a { image: "x:1" }', 'service a { image: "x:2" }')
        assert isinstance(json.loads(r.format_json()), dict)


class TestDiffJsonShape:
    def test_json_changed_shape(self):
        r = diff(
            'service api { image: "nginx:1.0" }', 'service api { image: "nginx:2.0" }'
        )
        data = json.loads(r.format_json())
        changed = data["changed"][0]
        assert changed["kind"] == "service"
        assert changed["name"] == "api"
        assert "field" in changed["changes"][0]
        assert "before" in changed["changes"][0]
        assert "after" in changed["changes"][0]
