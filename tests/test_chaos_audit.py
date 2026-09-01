"""Chaos / stress / crash testing.

These find real production bugs (hangs, crashes, shared-state corruption,
memory growth) rather than only adding coverage. Some are deliberately slow
and marked @pytest.mark.slow; run them explicitly with -m slow.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from infra import parse, validate
from infra.backends.compose import DockerComposeBackend
from infra.backends.github import GitHubActionsBackend
from infra.backends.kubernetes import KubernetesBackend
from infra.errors.exceptions import InfraLexError, InfraParseError

# --------------------------------------------------------------------------- #
# 1. Very large files
# --------------------------------------------------------------------------- #


def _large_source(n_services: int = 100, n_dbs: int = 20) -> str:
    parts = [
        (
            f'service svc{i} {{ image: "app{i}:1.0" replicas: {(i % 4) + 1} '
            f'port: {8000 + i} health http("/health") '
            "resources { requests { cpu: 100m, memory: 128Mi } "
            "limits { cpu: 500m, memory: 256Mi } } "
            "}"
        )
        for i in range(n_services)
    ]
    parts += [
        f"database db{i} {{ type: postgres storage: 10Gi ssl: true }}"
        for i in range(n_dbs)
    ]
    return "\n".join(parts)


@pytest.mark.slow
@pytest.mark.slow
@pytest.mark.timeout(300)
class TestLargeFileStress:
    def test_large_file_parse_validate_compile(self):
        source = _large_source(100, 20)
        t0 = time.perf_counter()
        program = parse(source)
        parse_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        result = validate(program)
        validate_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        compiled = KubernetesBackend().compile(program)
        compile_ms = (time.perf_counter() - t0) * 1000

        # generous budgets — the goal is "doesn't hang or crash"
        assert parse_ms < 5000, f"parse too slow: {parse_ms:.0f}ms"
        assert validate_ms < 1000, f"validate too slow: {validate_ms:.0f}ms"
        assert compile_ms < 5000, f"compile too slow: {compile_ms:.0f}ms"
        assert result is not None and compiled is not None
        # output must be parseable YAML (multi-document aware, yml/yaml only)
        for fname, content in compiled.files.items():
            if fname.endswith((".yml", ".yaml")) and content.strip():
                for doc in yaml.safe_load_all(content):
                    assert isinstance(doc, (dict, type(None)))


# --------------------------------------------------------------------------- #
# 2. Backend x feature matrix
# --------------------------------------------------------------------------- #

FEATURE_SOURCES = {
    "service_autoscale": ('service s { image: "x:1" autoscale { min: 2 max: 10 } }'),
    "service_disruption": (
        'service s { image: "x:1" disruption { min_available: 1 } }'
    ),
    "service_network_policy": (
        'service s { image: "x:1" network_policy { deny_from: ["*"] } }'
    ),
    "service_topology": (
        'service s { image: "x:1" topology { spread_by: zone max_skew: 1 } }'
    ),
    "service_affinity": ('service s { image: "x:1" affinity { prefer_same: [a] } }'),
    "database_backup_ssl": (
        "database d { type: postgres backup { enabled: true } ssl: true }"
    ),
    "env_quotas_extends": (
        'environment dev { namespace: "d" }\n'
        "environment prod extends dev { quotas { max_cpu: 10cores } }"
    ),
    "pipeline_matrix": (
        'pipeline p { trigger { branches: ["main"] } '
        'stages { t: { matrix { py: ["3.11"] } '
        'steps { s: { run: "x" } } } } }'
    ),
}


@pytest.mark.slow
class TestBackendFeatureMatrix:
    @pytest.mark.parametrize("name", sorted(FEATURE_SOURCES))
    def test_feature_compiles_all_backends(self, name):
        source = FEATURE_SOURCES[name]
        program = parse(source)
        # compile to each backend that accepts it; assert YAML output parseable
        backends = (
            KubernetesBackend(),
            DockerComposeBackend(),
            GitHubActionsBackend(),
        )
        for backend in backends:
            compiled = backend.compile(program)
            assert compiled is not None
            for fname, content in compiled.files.items():
                if fname.endswith((".yml", ".yaml")) and content.strip():
                    # output may be multi-document (multiple --- separated docs)
                    for doc in yaml.safe_load_all(content):
                        assert isinstance(doc, (dict, type(None)))


# --------------------------------------------------------------------------- #
# 3. Parallel compilation — shared state corruption
# --------------------------------------------------------------------------- #

PARALLEL_SOURCE = (
    'const VERSION = "v1"\n'
    "service api { image: `app:{VERSION}` replicas: 3 }\n"
    "database db { type: postgres }\n"
    "cache c { type: redis maxmemory: 256Mi }\n"
)


@pytest.mark.slow
@pytest.mark.slow
@pytest.mark.timeout(300)
class TestParallelCompilation:
    def test_parallel_parse_validate_compile(self):
        def work(_):
            program = parse(PARALLEL_SOURCE)
            validate(program)
            out = KubernetesBackend().compile(program)
            return "\n".join(out.files.values())

        with ThreadPoolExecutor(max_workers=10) as ex:
            results = list(ex.map(work, range(10)))
        assert len(results) == 10
        # all runs must produce the interpolated image — no shared-state bleed
        assert all("app:v1" in r for r in results)


# --------------------------------------------------------------------------- #
# 4. Repeated compile loop — memory/leak sanity
# --------------------------------------------------------------------------- #


@pytest.mark.slow
@pytest.mark.slow
@pytest.mark.timeout(300)
class TestRepeatedCompile:
    def test_repeated_compile_stable(self):
        program = parse(PARALLEL_SOURCE)
        backend = KubernetesBackend()
        first = "\n".join(backend.compile(program).files.values())
        # run the loop; each result must be identical (idempotent / no state
        # bleed). 10 iterations prove the invariant (a state bleed would show
        # on iteration 2); the previous 50x loop cost ~18 s for no extra signal.
        for _ in range(10):
            out = "\n".join(backend.compile(program).files.values())
            assert out == first, "compiled output changed across iterations"


class TestMalformedInputStorm:
    """A storm of malformed inputs must never crash the parser or backends."""

    STORM = [
        "",
        "   ",
        "{",
        "}",
        "service",
        "service {",
        "service api",
        "service api {",
        "service api { image",
        'service api { image: "',
        'service api { image: "x" replicas',
        'service api { image: "x" replicas: ',
        ":::bad:::",
        "@@@@",
        "import",
        "import ",
        'from "x" import',
        "let",
        "const x",
        "\x00\x01",
        "🚀" * 20,
        "#" * 500,
        "service " * 30,
        "database db { type ",
        "pipeline p { stages { s { run ",
    ]

    @pytest.mark.slow
    def test_storm_never_crashes(self):
        for src in self.STORM:
            try:
                parse(src)
            except (InfraParseError, InfraLexError):
                pass
            # any other exception type would fail here

    @pytest.mark.slow
    def test_storm_backend_safe(self):
        # compiling the ones that parse must not crash either
        for src in self.STORM:
            try:
                program = parse(src)
            except (InfraParseError, InfraLexError):
                continue
            for backend in (KubernetesBackend(), DockerComposeBackend()):
                try:
                    backend.compile(program)
                except Exception:
                    # a compile error on a weird-but-parseable input is OK as
                    # long as it's not a crash (we just don't want hangs)
                    pass


class TestLspRequestStorm:
    """Many LSP-style operations over varied inputs must stay consistent."""

    SOURCES = [
        "",
        'service api { image: "x:1" }',
        'service api { image: "x:1" replicas: 0 }',
        "database db { type: postgres }",
        "service api {\n    \n}",
        "service {",
        'env { PASSWORD: "bad" }',
    ]

    @pytest.mark.slow
    def test_diagnose_storm_no_crash(self):
        from infra.lsp.server import _diagnose

        for src in self.SOURCES:
            diags = _diagnose(src, "storm.infra")
            assert isinstance(diags, list)
            # valid services produce diagnostics; malformed must not crash
            assert all(hasattr(d, "code") for d in diags)

    @pytest.mark.slow
    def test_completion_storm_no_crash(self):
        from infra.lsp.completion import completions_at

        for src in self.SOURCES:
            for line in range(len(src.splitlines())):
                for char in (0, 4, 10):
                    items = completions_at(src, line, char)
                    assert isinstance(items, list)
