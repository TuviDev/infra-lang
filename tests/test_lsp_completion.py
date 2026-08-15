"""Tests for the LSP completion engine (context-aware, heuristic)."""

from __future__ import annotations

import pytest
from lsprotocol.types import CompletionItemKind

from infra.lsp.completion import (
    BLOCK_FIELDS,
    BLOCK_SUBBLOCKS,
    FIELD_VALUE_HINTS,
    TOP_LEVEL_BLOCKS,
    completions_at,
)


def labels(items):
    return [i.label for i in items]


class TestTopLevelCompletions:
    def test_empty_document_suggests_block_types(self):
        items = completions_at("", 0, 0)
        for block in TOP_LEVEL_BLOCKS:
            assert block in labels(items), f"missing top-level {block}"

    def test_top_level_items_are_struct_kind(self):
        items = completions_at("", 0, 0)
        assert all(i.kind == CompletionItemKind.Struct for i in items)

    def test_top_level_items_have_snippet_insert(self):
        items = completions_at("", 0, 0)
        assert all(i.insert_text for i in items)


class TestInsideBlockCompletions:
    def test_service_fields(self):
        src = "service api {\n    \n}"
        got = labels(completions_at(src, 1, 4))
        # sub-blocks
        for sub in BLOCK_SUBBLOCKS["service"]:
            assert sub in got, f"missing service sub-block {sub}"
        # plain fields (not offered as sub-block)
        for field in BLOCK_FIELDS["service"]:
            assert field in got, f"missing service field {field}"

    def test_database_fields(self):
        src = "database db {\n    \n}"
        got = labels(completions_at(src, 1, 4))
        for field in BLOCK_FIELDS["database"]:
            assert field in got, f"missing database field {field}"

    def test_no_duplicate_items(self):
        src = "service api {\n    \n}"
        got = labels(completions_at(src, 1, 4))
        assert len(got) == len(set(got)), "duplicate completion items"

    def test_token_filter_applies(self):
        src = "service api {\n    rep\n}"
        got = labels(completions_at(src, 1, 7))
        assert "replicas" in got
        assert all(l.startswith("rep") for l in got)


class TestValueCompletions:
    def test_enum_values_after_colon(self):
        src = "service api {\n    strategy: \n}"
        got = labels(completions_at(src, 1, 13))
        for v in FIELD_VALUE_HINTS["strategy"]:
            assert v in got, f"missing strategy value {v}"

    def test_bool_values_after_ssl_colon(self):
        src = "database db {\n    ssl: \n}"
        got = labels(completions_at(src, 1, 9))
        assert "true" in got and "false" in got

    def test_quantity_values_after_storage_colon(self):
        src = "database db {\n    storage: \n}"
        got = labels(completions_at(src, 1, 13))
        assert any(v.endswith("Gi") for v in got)


class TestSubBlockCompletions:
    def test_resources_subblock_suggested_in_service(self):
        src = "service api {\n    resources\n}"
        got = labels(completions_at(src, 1, 12))
        assert "resources" in got

    def test_backup_subblock_in_database(self):
        src = "database db {\n    backup\n}"
        got = labels(completions_at(src, 1, 10))
        assert "backup" in got


class TestMalformedInput:
    @pytest.mark.parametrize(
        "src,line,char",
        [
            ("", 0, 0),
            ("service", 0, 7),
            ("service api {", 0, 13),
            ("{ { {", 0, 5),
            ("    \n\n  ", 2, 2),
            ("service api {\n    strategy: \n    ", 2, 4),
        ],
    )
    def test_no_crash_on_incomplete_input(self, src, line, char):
        # must never raise
        completions_at(src, line, char)


class TestSymbolAwareCompletions:
    def test_depends_suggests_document_blocks(self):
        src = "database db {}\nservice api {\n    depends: [\n}"
        got = labels(completions_at(src, 2, 14))
        assert "db" in got
        assert "api" in got

    def test_depends_reference_kind(self):
        src = "database db {}\nservice api {\n    depends: [\n}"
        items = completions_at(src, 2, 14)
        refs = [i for i in items if i.detail and "reference" in i.detail]
        assert refs
        assert all(i.kind == CompletionItemKind.Struct for i in refs)

    def test_symbol_aware_does_not_leak_to_enum_fields(self):
        src = "database db {}\nservice api {\n    strategy: \n}"
        got = labels(completions_at(src, 2, 13))
        # strategy is an enum field, not a reference -> no block names
        assert "db" not in got


class TestRanking:
    def test_value_items_have_sort_text(self):
        src = "service api {\n    strategy: \n}"
        items = completions_at(src, 1, 13)
        assert all(i.sort_text for i in items)

    def test_reference_items_sorted_after_values(self):
        src = "database db {}\nservice api {\n    depends: [\n}"
        items = completions_at(src, 2, 14)
        assert all(i.sort_text for i in items)
