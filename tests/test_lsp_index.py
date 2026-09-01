"""Unit tests for the on-disk WorkspaceIndex and its helpers."""

from __future__ import annotations

import pytest

try:
    from infra.lsp.workspace_index import (
        KIND_TO_SYMBOL_KIND,
        WorkspaceIndex,
        _scan_source,
        find_references_in_sources,
    )

    HAS_LSP = True
except ImportError:  # pragma: no cover - pygls not installed
    HAS_LSP = False

pytestmark = pytest.mark.skipif(not HAS_LSP, reason="pygls not installed")


class TestScanSource:
    def test_extracts_blocks(self):
        syms = _scan_source("service api {}\ndatabase db {}\n", "file:///x")
        names = {(s.name, s.kind) for s in syms}
        assert names == {("api", "service"), ("db", "database")}

    def test_ignores_comments(self):
        syms = _scan_source("# service a {}\nservice b {}\n", "file:///x")
        assert [(s.name, s.kind) for s in syms] == [("b", "service")]

    def test_malformed_no_crash(self):
        syms = _scan_source("service { unclosed\n%%%\n", "file:///x")
        assert syms == []


class TestWorkspaceIndexScan:
    def test_recursive_scan(self, tmp_path):
        (tmp_path / "a.infra").write_text("service api {}\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.infra").write_text("database db {}\n")
        idx = WorkspaceIndex()
        idx.scan_directory(tmp_path)
        assert len(idx.sources()) == 2
        names = {(s.name, s.kind) for s in idx.all_symbols()}
        assert names == {("api", "service"), ("db", "database")}

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "x.infra").write_text("service a {}\n")
        (tmp_path / "real.infra").write_text("service b {}\n")
        idx = WorkspaceIndex()
        idx.scan_directory(tmp_path)
        assert len(idx.sources()) == 1
        assert "b" in {s.name for s in idx.all_symbols()}

    def test_skips_huge_files(self, tmp_path):
        (tmp_path / "big.infra").write_text("x" * 2_000_000)
        (tmp_path / "ok.infra").write_text("service a {}\n")
        idx = WorkspaceIndex()
        idx.scan_directory(tmp_path)
        assert len(idx.sources()) == 1  # big file excluded by size cap

    def test_malformed_files_skipped(self, tmp_path):
        (tmp_path / "bad.infra").write_text("service { unclosed\n%%%")
        (tmp_path / "good.infra").write_text("service a {}\n")
        idx = WorkspaceIndex()
        idx.scan_directory(tmp_path)
        # malformed file is still indexed for symbols (no crash), good one too
        assert len(idx.sources()) == 2

    def test_non_existent_root_no_crash(self):
        idx = WorkspaceIndex()
        idx.scan_directory(__import__("tempfile").mkdtemp())
        idx.scan_directory(__import__("pathlib").Path("/definitely/not/here"))
        assert idx.sources() == {}


class TestWorkspaceIndexMutation:
    def test_add_and_remove(self):
        idx = WorkspaceIndex()
        idx.add_file("file:///a.infra", "service api {}\n")
        assert "api" in {s.name for s in idx.all_symbols()}
        idx.add_file("file:///a.infra", "service web {}\n")
        names = {s.name for s in idx.all_symbols()}
        assert names == {"web"}  # refresh replaced api with web
        idx.remove_file("file:///a.infra")
        assert idx.all_symbols() == []

    def test_clear(self):
        idx = WorkspaceIndex()
        idx.add_file("file:///a.infra", "service api {}\n")
        idx.clear()
        assert idx.sources() == {}
        assert idx.all_symbols() == []

    def test_definitions_by_name(self):
        idx = WorkspaceIndex()
        idx.add_file("file:///a.infra", "service api {}\n")
        idx.add_file("file:///b.infra", "service api {}\n")
        defs = idx.definitions("api")
        assert len(defs) == 2
        assert {d.uri for d in defs} == {"file:///a.infra", "file:///b.infra"}


class TestFindReferencesInSources:
    def test_pre_filter_and_ranges(self):
        sources = {
            "file:///a.infra": "service api {\n  depends: [db]\n}\n",
            "file:///b.infra": "database db {}\n",
        }
        locs = find_references_in_sources(sources, "db")
        # a.infra has the reference; b.infra is skipped as a reference (it's
        # the definition line, reference_ranges excludes it)
        assert len(locs) == 1
        assert locs[0].uri == "file:///a.infra"

    def test_absent_name_returns_empty(self):
        assert (
            find_references_in_sources({"file:///a.infra": "service x {}\n"}, "zzz")
            == []
        )


class TestKindMapping:
    def test_all_block_kinds_mapped(self):
        # every top-level block keyword maps to a SymbolKind name
        for kw in (
            "service",
            "database",
            "cache",
            "queue",
            "storage",
            "network",
            "secret",
            "config",
            "pipeline",
            "environment",
            "cluster",
        ):
            assert KIND_TO_SYMBOL_KIND.get(kw), f"{kw} missing mapping"


class TestWorkspaceIndexEdgeBranches:
    def test_max_files_cap_breaks_scan(self, tmp_path):
        from infra.lsp.workspace_index import MAX_FILES

        for i in range(MAX_FILES + 2):
            (tmp_path / f"f{i}.infra").write_text("service a {}\n")
        idx = WorkspaceIndex()
        idx.scan_directory(tmp_path)
        assert len(idx.sources()) <= MAX_FILES  # capped, no crash

    def test_add_file_size_guard(self):
        idx = WorkspaceIndex()
        idx.add_file("file:///big.infra", "x" * (2_000_000))
        assert idx.sources() == {}  # oversized source not indexed

    def test_remove_file_keeps_other_defs(self):
        idx = WorkspaceIndex()
        idx.add_file("file:///a.infra", "service api {}\n")
        idx.add_file("file:///b.infra", "service api {}\n")
        assert len(idx.definitions("api")) == 2
        idx.remove_file("file:///a.infra")
        # b.infra still defines api -> kept branch
        defs = idx.definitions("api")
        assert len(defs) == 1 and defs[0].uri == "file:///b.infra"

    def test_remove_file_last_def_pops(self):
        idx = WorkspaceIndex()
        idx.add_file("file:///a.infra", "service api {}\n")
        idx.remove_file("file:///a.infra")
        assert idx.definitions("api") == []
