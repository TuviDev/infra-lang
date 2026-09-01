"""Performance budget tests.

These are GUARANTEED performance characteristics enforced in CI. Every limit
is a named constant (not a magic number). If a budget is exceeded, the suite
fails — see docs/performance_budgets.md.
"""

from __future__ import annotations

import time

import pytest

from infra import parse, validate
from infra.backends.compose import DockerComposeBackend
from infra.backends.kubernetes import KubernetesBackend

# --------------------------------------------------------------------------- #
# Budgets (named constants, enforced in CI)
# --------------------------------------------------------------------------- #

PARSE_SMALL_LIMIT_MS = 100  # single service
PARSE_LARGE_LIMIT_MS = 2000  # 20+ definitions
VALIDATE_LIMIT_MS = 500  # per program
COMPILE_K8S_LIMIT_MS = 1000  # per program
COMPILE_ALL_BACKENDS_MS = 3000
FULL_PIPELINE_LIMIT_MS = 2000  # parse + validate + compile

SMALL_SOURCE = 'service api { image: "nginx:1.25" }'

LARGE_SOURCE = "\n".join(
    [
        (
            f"service svc{i} {{"
            f'  image: "app{i}:v1.0" replicas: {(i % 4) + 1}'
            f'  port: {8000 + i} health: http("/health")'
            f"}}"
        )
        for i in range(20)
    ]
) + "\n".join([f"database db{i} {{ type: postgres storage: 10Gi }}" for i in range(5)])


def _avg_ms(fn, n: int) -> float:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return sum(times) / len(times)


@pytest.mark.slow
class TestPerformanceBudgets:
    def test_parse_small_within_budget(self):
        avg = _avg_ms(lambda: parse(SMALL_SOURCE), 20)
        assert avg < PARSE_SMALL_LIMIT_MS, (
            f"Parse small: {avg:.1f}ms exceeds budget {PARSE_SMALL_LIMIT_MS}ms"
        )

    def test_parse_large_within_budget(self):
        t0 = time.perf_counter()
        parse(LARGE_SOURCE)
        elapsed = (time.perf_counter() - t0) * 1000
        assert elapsed < PARSE_LARGE_LIMIT_MS, (
            f"Parse large: {elapsed:.1f}ms exceeds budget {PARSE_LARGE_LIMIT_MS}ms"
        )

    def test_validate_within_budget(self):
        program = parse(LARGE_SOURCE)
        t0 = time.perf_counter()
        validate(program)
        elapsed = (time.perf_counter() - t0) * 1000
        assert elapsed < VALIDATE_LIMIT_MS, (
            f"Validate: {elapsed:.1f}ms exceeds budget {VALIDATE_LIMIT_MS}ms"
        )

    def test_compile_k8s_within_budget(self):
        program = parse(LARGE_SOURCE)
        t0 = time.perf_counter()
        KubernetesBackend().compile(program)
        elapsed = (time.perf_counter() - t0) * 1000
        assert elapsed < COMPILE_K8S_LIMIT_MS, (
            f"Compile K8s: {elapsed:.1f}ms exceeds budget {COMPILE_K8S_LIMIT_MS}ms"
        )

    def test_full_pipeline_within_budget(self):
        t0 = time.perf_counter()
        program = parse(LARGE_SOURCE)
        validate(program)
        KubernetesBackend().compile(program)
        elapsed = (time.perf_counter() - t0) * 1000
        assert elapsed < FULL_PIPELINE_LIMIT_MS, (
            f"Full pipeline: {elapsed:.1f}ms exceeds budget {FULL_PIPELINE_LIMIT_MS}ms"
        )

    def test_compile_all_backends_within_budget(self):
        program = parse(LARGE_SOURCE)
        t0 = time.perf_counter()
        KubernetesBackend().compile(program)
        DockerComposeBackend().compile(program)
        elapsed = (time.perf_counter() - t0) * 1000
        assert elapsed < COMPILE_ALL_BACKENDS_MS, (
            f"All backends: {elapsed:.1f}ms exceeds budget {COMPILE_ALL_BACKENDS_MS}ms"
        )
