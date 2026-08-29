"""Edge-case tests for infra.config feedback parsing & writing (v0.5.3)."""

from __future__ import annotations

import yaml

from infra.config import _parse_feedback, write_config


class TestParseFeedbackCoercions:
    def test_bool_passthrough(self) -> None:
        assert _parse_feedback(True) is True
        assert _parse_feedback(False) is False

    def test_string_truthy_variants(self) -> None:
        for raw in ("true", "TRUE", " yes ", "1", "On"):
            assert _parse_feedback(raw) is True, raw

    def test_string_falsy_variants(self) -> None:
        for raw in ("false", "no", "0", "off", "", "random"):
            assert _parse_feedback(raw) is False, raw

    def test_int_coercion(self) -> None:
        assert _parse_feedback(1) is True
        assert _parse_feedback(42) is True
        assert _parse_feedback(0) is False

    def test_other_types_fallback(self) -> None:
        assert _parse_feedback(None) is False
        assert _parse_feedback([]) is False
        assert _parse_feedback({}) is False


class TestWriteConfig:
    def test_extra_keys_are_merged(self, tmp_path) -> None:
        path = tmp_path / "sub" / ".infra-config.yaml"
        write_config(path, True, extra={"team": "infra", "level": 2})
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["feedback"] == {"enabled": True}
        assert data["team"] == "infra"
        assert data["level"] == 2

    def test_existing_keys_preserved(self, tmp_path) -> None:
        path = tmp_path / ".infra-config.yaml"
        path.write_text("name: demo\n", encoding="utf-8")
        write_config(path, False)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["name"] == "demo"
        assert data["feedback"] == {"enabled": False}
