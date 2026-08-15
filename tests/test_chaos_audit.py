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

# --------------------------------------------------------------------------- #
# 1. Very large files
# --------------------------------------------------------------------------- #


def _large_source(n_services: int = 100, n_dbs: int = 20) -> str:
    parts = [
        (
            f"service svc{i} {{ image: \"app{i}:1.0\" replicas: {(i % 4) + 1} "
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
    "service_autoscale": 'service s { image: "x:1" autoscale { min: 2 max: 10 } }',
    "service_disruption": 'service s { image: "x:1" disruption { min_available: 1 } }',
    "service_network_policy": 'service s { image: "x:1" network_policy { deny_from: ["*"] } }',
    "service_topology": 'service s { image: "x:1" topology { spread_by: zone max_skew: 1 } }',
    "service_affinity": 'service s { image: "x:1" affinity { prefer_same: [a] } }',
    "database_backup_ssl": "database d { type: postgres backup { enabled: true } ssl: true }",
    "env_quotas_extends": "environment dev { namespace: \"d\" }\nenvironment prod extends dev { quotas { max_cpu: 10cores } }",
    "pipeline_matrix": 'pipeline p { trigger { branches: ["main"] } stages { t: { matrix { py: ["3.11"] } steps { s: { run: "x" } } } } }',
}


@pytest.mark.slow
class TestBackendFeatureMatrix:
    @pytest.mark.parametrize("name", sorted(FEATURE_SOURCES))
    def test_feature_compiles_all_backends(self, name):
        source = FEATURE_SOURCES[name]
        program = parse(source)
        # parse + validate
        result = validate(program)
        # compile to each backend that accepts it; assert YAML output parseable
        for backend in (KubernetesBackend(), DockerComposeBackend(), GitHubActionsBackend()):
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
    "const VERSION = \"v1\"\n"
    'service api { image: `app:{VERSION}` replicas: 3 }\n'
    "database db { type: postgres }\n"
    "cache c { type: redis maxmemory: 256Mi }\n"
)


@pytest.mark.slow
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
class TestRepeatedCompile:
    def test_repeated_compile_stable(self):
        program = parse(PARALLEL_SOURCE)
        backend = KubernetesBackend()
        first = "\n".join(backend.compile(program).files.values())
        # run the loop; each result must be identical (idempotent / no state bleed)
        for _ in range(50):
            out = "\n".join(backend.compile(program).files.values())
            assert out == first, "compiled output changed across iterations"
