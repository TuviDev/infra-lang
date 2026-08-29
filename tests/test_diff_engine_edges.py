"""Edge-case tests for infra.diff.engine formatting helpers (v0.5.3)."""

from __future__ import annotations

from infra.diff.engine import (
    ChangedItem,
    DiffItem,
    DiffResult,
    FieldChange,
    _compare_nodes,
    _node_value,
)
from infra.parser import ast_nodes as n


class TestFieldChangeFormat:
    def test_none_renders_as_null(self) -> None:
        ch = FieldChange("replicas", None, 3)
        assert ch.format() == "  ~ replicas: null → 3"

    def test_bool_renders_lowercase(self) -> None:
        ch = FieldChange("ha", True, False)
        assert ch.format() == "  ~ ha: true → false"


class TestDiffResultFormatColor:
    def _result(self) -> DiffResult:
        return DiffResult(
            added=[DiffItem("service", "api")],
            removed=[DiffItem("cache", "redis")],
            changed=[
                ChangedItem(
                    "database", "db", [FieldChange("version", "14", "15")]
                )
            ],
            unchanged=["queue q"],
        )

    def test_color_true_wraps_ansi_codes(self) -> None:
        out = self._result().format(color=True)
        assert "\033[33m" in out  # yellow summary
        assert "\033[32m+ service api (new)\033[0m" in out
        assert "\033[31m- cache redis (removed)\033[0m" in out
        assert "\033[37m= queue q (unchanged)\033[0m" in out

    def test_color_false_has_no_ansi(self) -> None:
        out = self._result().format(color=False)
        assert "\033[" not in out
        assert "+ service api (new)" in out
        assert "- cache redis (removed)" in out

    def test_only_changes_hides_unchanged(self) -> None:
        out = self._result().format(color=False, only_changes=True)
        assert "unchanged" not in out

    def test_no_changes_shows_checkmark(self) -> None:
        out = DiffResult().format(color=False)
        assert "No differences found" in out

    def test_no_changes_lists_unchanged_unless_only_changes(self) -> None:
        res = DiffResult(unchanged=["service api"])
        assert "= service api (unchanged)" in res.format(color=False)
        assert "unchanged" not in res.format(color=False, only_changes=True)


class TestNodeValueCoercions:
    def test_literal_unwrapped(self) -> None:
        assert _node_value(n.Literal("abc")) == "abc"

    def test_resource_value_combines_unit(self) -> None:
        # _node_value renders raw ``value`` + ``unit`` (no normalization).
        assert _node_value(n.ResourceValue(128.0, "Mi")) == "128.0Mi"

    def test_duration_combines_unit(self) -> None:
        assert _node_value(n.Duration(30.0, "s")) == "30.0s"

    def test_identifier_uses_name(self) -> None:
        assert _node_value(n.Identifier("MY_CONST")) == "MY_CONST"

    def test_none_stays_none(self) -> None:
        assert _node_value(None) is None

    def test_primitives_passthrough(self) -> None:
        assert _node_value(42) == 42
        assert _node_value(True) is True

    def test_unknown_falls_back_to_repr(self) -> None:
        obj = object()
        assert _node_value(obj) == repr(obj)


class TestCompareNodes:
    def test_type_mismatch_reports_field_change(self) -> None:
        changes = _compare_nodes(n.Literal(1), n.Identifier("x"), "image")
        assert len(changes) == 1
        assert changes[0].field_path == "image"
        assert changes[0].before == "Literal"
        assert changes[0].after == "Identifier"

    def test_type_mismatch_without_prefix_uses_type_label(self) -> None:
        changes = _compare_nodes(1, "a")
        assert len(changes) == 1
        assert changes[0].field_path == "type"

    def test_equal_scalars_produce_no_changes(self) -> None:
        assert _compare_nodes(5, 5, "port") == []
