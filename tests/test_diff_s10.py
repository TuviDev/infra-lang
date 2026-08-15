"""Session 10 - Diff improvements (Zadanie 5)."""

from __future__ import annotations

import json

from infra import parse
from infra.diff.engine import InfraDiff


def _diff(src1: str, src2: str):
    return InfraDiff().diff(parse(src1), parse(src2))


class TestDiffSummary:
    def test_json_summary_present(self):
        r = _diff('service api { image: "nginx:1.0" }',
                  'service api { image: "nginx:2.0" }')
        data = json.loads(r.format_json())
        assert "summary" in data
        assert data["summary"]["changed"] == 1
        assert data["summary"]["added"] == 0
        assert data["summary"]["removed"] == 0

    def test_json_summary_counts(self):
        r = _diff(
            'service api { image: "nginx:1.0" }',
            'service api { image: "nginx:2.0" }\n'
            'service worker { image: "redis:7" }\n',
        )
        data = json.loads(r.format_json())
        assert data["summary"] == {"changed": 1, "added": 1, "removed": 0}

    def test_text_summary_line(self):
        r = _diff('service api { image: "nginx:1.0" }',
                  'service api { image: "nginx:2.0" }')
        text = r.format(color=False)
        assert "SUMMARY:" in text
        assert "1 changed, 0 added, 0 removed" in text

    def test_no_differences_message(self):
        r = _diff('service api { image: "nginx:1.0" }',
                  'service api { image: "nginx:1.0" }')
        assert "No differences found" in r.format(color=False)

    def test_changed_before_after_values(self):
        r = _diff('service api { image: "nginx:1.0" }',
                  'service api { image: "nginx:2.0" }')
        c = next(c for c in r.changed if c.name == "api")
        img = next(ch for ch in c.changes if "image" in ch.field_path)
        assert img.before == "nginx:1.0"
        assert img.after == "nginx:2.0"

    def test_format_json_is_valid(self):
        r = _diff('service a { image: "x:1" }', 'service a { image: "x:2" }')
        assert isinstance(json.loads(r.format_json()), dict)


class TestDiffJsonShape:
    def test_json_changed_shape(self):
        r = _diff('service api { image: "nginx:1.0" }',
                  'service api { image: "nginx:2.0" }')
        data = json.loads(r.format_json())
        changed = data["changed"][0]
        assert changed["kind"] == "service"
        assert changed["name"] == "api"
        assert "field" in changed["changes"][0]
        assert "before" in changed["changes"][0]
        assert "after" in changed["changes"][0]
