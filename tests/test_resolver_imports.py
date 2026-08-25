"""Contract tests for the import resolver fixes (v0.4.4, package 1).

Regression contracts for the three audit findings in
``src/infra/resolver/imports.py``:

* diamond imports must not duplicate shared symbols (false ``E001``),
* the recursion guard must raise a domain error, never ``RecursionError``,
* the Lark parser instance is constructed once per process (cached).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from infra import parse
from infra.errors.exceptions import InfraError
from infra.parser import parse_file
from infra.parser.ast_nodes import VariableDecl
from infra.resolver.imports import (
    DEFAULT_MAX_DEPTH,
    ImportCycleError,
    ImportDepthError,
    ImportResolver,
)


def _write(root: Path, name: str, content: str) -> Path:
    p = root / name
    p.write_text(content, encoding="utf-8")
    return p


def _make_diamond(root: Path) -> Path:
    """a imports b and c; b and c both import d (which defines X + helpers)."""
    _write(
        root,
        "d.infra",
        'const X = 1\nconst Y = 2\nservice shared { image: "alpine:3.20" }',
    )
    _write(root, "b.infra", 'import "./d.infra"\nservice bs { image: "nginx:1.25" }')
    _write(root, "c.infra", 'import "./d.infra"\nservice cs { image: "redis:7" }')
    return _write(
        root,
        "a.infra",
        'import "./b.infra"\nimport "./c.infra"\nservice api { image: "myapi:v1" }',
    )


class TestDiamondImports:
    def test_diamond_no_duplicate_variable_error(self, tmp_path):
        """The audit reproducer: a diamond must not yield a false E001."""
        root = _make_diamond(tmp_path)
        program = parse_file(root)
        from infra import validate

        result = validate(program)
        dups = [e for e in result.errors if e.code in ("E001", "E002")]
        assert result.is_valid, [e.message for e in result.errors]
        assert not dups

    def test_diamond_merges_each_definition_once(self, tmp_path):
        root = _make_diamond(tmp_path)
        program = parse_file(root)
        names = [getattr(s, "name", None) for s in program.statements]
        # every file's definition appears exactly once
        assert names.count("X") == 1
        assert names.count("Y") == 1
        assert names.count("shared") == 1
        assert names.count("bs") == 1
        assert names.count("cs") == 1
        assert names.count("api") == 1

    def test_diamond_symbols_available_everywhere(self, tmp_path):
        """The shared const resolves in services from both branches."""
        root = _make_diamond(tmp_path)
        program = parse_file(root)
        from infra.backends.kubernetes import KubernetesBackend

        files = KubernetesBackend().compile(program).files
        assert files  # compiles end-to-end

    def test_selective_then_full_import_union(self, tmp_path):
        """`from d import X` in one file, full `import d` elsewhere: both
        X and Y stay available exactly once — no loss, no duplication."""
        _write(tmp_path, "d.infra", "const X = 1\nconst Y = 2")
        _write(
            tmp_path,
            "b.infra",
            'from "./d.infra" import X\nservice bs { image: "nginx:1.25" }',
        )
        _write(
            tmp_path,
            "c.infra",
            'import "./d.infra"\nservice cs { image: "redis:7" }',
        )
        root = _write(
            tmp_path,
            "a.infra",
            'import "./b.infra"\nimport "./c.infra"\nservice api { image: "x" }',
        )
        program = parse_file(root)
        vars_seen = [
            s.name for s in program.statements if isinstance(s, VariableDecl)
        ]
        assert vars_seen.count("X") == 1
        assert vars_seen.count("Y") == 1  # full import still delivers Y

    def test_overlapping_selective_imports_deduplicate(self, tmp_path):
        """`from d import X` + `from d import X, Y`: X merges once."""
        _write(tmp_path, "d.infra", "const X = 1\nconst Y = 2")
        root = _write(
            tmp_path,
            "a.infra",
            'from "./d.infra" import X\nfrom "./d.infra" import X, Y\n'
            'service api { image: "x" }',
        )
        program = parse_file(root)
        vars_seen = [
            s.name for s in program.statements if isinstance(s, VariableDecl)
        ]
        assert vars_seen.count("X") == 1
        assert vars_seen.count("Y") == 1

    def test_repeated_identical_import_skipped(self, tmp_path):
        """`import "d"` twice: merged once (fix), W004 warning preserved."""
        _write(tmp_path, "d.infra", "const X = 1")
        root = _write(
            tmp_path,
            "a.infra",
            'import "./d.infra"\nimport "./d.infra"\nservice api { image: "x" }',
        )
        program = parse_file(root)
        vars_seen = [
            s.name for s in program.statements if isinstance(s, VariableDecl)
        ]
        assert vars_seen.count("X") == 1
        from infra import validate

        result = validate(program)
        assert not any(e.code == "E001" for e in result.errors)
        # the documented W004 duplicate-import warning is still emitted
        assert any(w.code == "W004" for w in result.warnings)

    def test_cycle_detection_still_works(self, tmp_path):
        """Regression: diamonds must not have broken the cycle guard."""
        _write(tmp_path, "a.infra", 'import "./b.infra"\nservice a { image: "x" }')
        _write(tmp_path, "b.infra", 'import "./a.infra"\nservice b { image: "y" }')
        with pytest.raises(ImportCycleError):
            parse_file(tmp_path / "a.infra")

    def test_self_import_is_a_cycle(self, tmp_path):
        _write(tmp_path, "s.infra", 'import "./s.infra"\nservice s { image: "x" }')
        with pytest.raises(ImportCycleError):
            parse_file(tmp_path / "s.infra")

    def test_genuinely_duplicate_files_still_error(self, tmp_path):
        """Same name defined in TWO different files is a real conflict."""
        _write(tmp_path, "d1.infra", "const X = 1")
        _write(tmp_path, "d2.infra", "const X = 2")
        root = _write(
            tmp_path,
            "a.infra",
            'import "./d1.infra"\nimport "./d2.infra"\nservice api { image: "x" }',
        )
        program = parse_file(root)
        from infra import validate

        result = validate(program)
        assert any(e.code == "E001" for e in result.errors)


def _make_chain(root: Path, length: int) -> Path:
    """Create root → f1 → ... → f{length} import chain, return the root file."""
    for i in range(1, length + 1):
        nxt = f'import "./f{i + 1}.infra"\n' if i < length else ""
        _write(root, f"f{i}.infra", f'{nxt}service s{i} {{ image: "x" }}')
    return _write(
        root, "main.infra", 'import "./f1.infra"\nservice app { image: "x" }'
    )


class TestDepthGuard:
    def test_chain_at_limit_passes(self, tmp_path):
        """Exactly max_depth nested hops (root not counted) must pass."""
        root = _make_chain(tmp_path, DEFAULT_MAX_DEPTH)  # 20 nested files
        program = parse_file(root)
        names = [getattr(s, "name", None) for s in program.statements]
        assert names.count("app") == 1
        assert names.count(f"s{DEFAULT_MAX_DEPTH}") == 1

    def test_chain_beyond_limit_raises_domain_error(self, tmp_path):
        """21 nested hops must raise ImportDepthError — never RecursionError."""
        root = _make_chain(tmp_path, DEFAULT_MAX_DEPTH + 2)
        with pytest.raises(ImportDepthError) as exc_info:
            parse_file(root)
        expected = f"Import depth exceeded limit of {DEFAULT_MAX_DEPTH}"
        assert exc_info.value.message == expected
        assert expected in str(exc_info.value)
        assert isinstance(exc_info.value, InfraError)
        assert not isinstance(exc_info.value, RecursionError)

    def test_very_deep_chain_still_domain_error(self, tmp_path):
        """A comically deep chain fails at the guard, not at CPython's limit."""
        root = _make_chain(tmp_path, 60)
        try:
            parse_file(root)
        except Exception as exc:  # noqa: BLE001 - the whole point: no crash
            assert type(exc) is ImportDepthError
        else:  # pragma: no cover - guarded above
            pytest.fail("expected ImportDepthError")

    def test_message_names_the_limit(self, tmp_path):
        root = _make_chain(tmp_path, 5)
        resolver = ImportResolver(base_path=tmp_path, max_depth=3)
        program = parse(root.read_text(encoding="utf-8"))
        with pytest.raises(ImportDepthError, match=r"limit of 3"):
            resolver.resolve(program, root)

    def test_custom_max_depth_still_configurable(self, tmp_path):
        """Backward compat: the max_depth constructor parameter keeps working."""
        root = _make_chain(tmp_path, 3)  # shallow chain
        resolver = ImportResolver(base_path=tmp_path, max_depth=3)
        program = parse(root.read_text(encoding="utf-8"))
        resolved = resolver.resolve(program, root)
        assert resolved.statements  # resolves fine within a custom limit


class TestParserCachePerformance:
    """The Lark parser instance is built once per process, not per import.

    Audit finding: re-compiling ``grammar.lark`` costs ~0.7 s, so 20 imports
    took ~14.5 s. With the shared instance the same workload must fit well
    under the 1.5 s budget.
    """

    PERF_BUDGET_SECONDS = 1.5
    IMPORT_COUNT = 20

    def test_raw_lark_is_a_process_wide_singleton(self):
        from infra.parser import _raw_lark

        first = _raw_lark()
        second = _raw_lark()
        assert first is second

    def test_parser_instances_share_the_lark_backend(self):
        """Two default-grammar ``Parser`` objects must reuse one Lark."""
        from infra.parser import Parser

        assert Parser()._lark is Parser()._lark

    def test_custom_grammar_still_builds_its_own_lark(self, tmp_path):
        """Backward compat: an explicit grammar path bypasses the cache."""
        from infra.parser import DEFAULT_GRAMMAR, Parser, _raw_lark

        custom = tmp_path / "grammar.lark"
        custom.write_text(
            DEFAULT_GRAMMAR.read_text(encoding="utf-8"), encoding="utf-8"
        )
        parser = Parser(grammar_path=custom)
        assert parser._lark is not _raw_lark()

    def _make_many_imports(self, root: Path, count: int) -> Path:
        for i in range(1, count + 1):
            _write(
                root,
                f"mod{i}.infra",
                f'const V{i} = {i}\nservice s{i} {{ image: "alpine:3.20" }}',
            )
        lines = [f'import "./mod{i}.infra"' for i in range(1, count + 1)]
        lines.append('service root { image: "nginx:1.25" }')
        return _write(root, "main.infra", "\n".join(lines))

    def test_twenty_imports_within_budget(self, tmp_path):
        from infra.parser import _raw_lark

        # Warm the singleton up front: the one-time ~0.7 s grammar compile is
        # amortised by definition; what we measure is the per-import cost.
        _raw_lark()

        root = self._make_many_imports(tmp_path, self.IMPORT_COUNT)
        start = time.perf_counter()
        program = parse_file(root)
        elapsed = time.perf_counter() - start

        assert elapsed < self.PERF_BUDGET_SECONDS, (
            f"{self.IMPORT_COUNT} imports took {elapsed:.2f}s "
            f"(budget {self.PERF_BUDGET_SECONDS}s); "
            "the Lark parser must be cached, not recompiled per import"
        )
        # sanity: all imported symbols really resolved (the program also
        # carries the stdlib prelude, so check containment, not equality)
        names = {
            s.name
            for s in program.statements
            if isinstance(s, VariableDecl)
        }
        assert {f"V{i}" for i in range(1, self.IMPORT_COUNT + 1)} <= names
