#!/usr/bin/env python3
"""Benchmark suite for Infra Lang.

Measures parse/compile/memory/LSP latency over representative inputs and writes
a readable report. Run with:

    python scripts/benchmark.py            # print to stdout
    python scripts/benchmark.py --save     # also write docs/benchmark_baseline.md

The numbers are the source of truth for docs/benchmark_baseline.md.
"""

from __future__ import annotations

import argparse
import sys
import time
import tracemalloc
from pathlib import Path

from infra import parse
from infra.backends import get_backend

SMALL = 'service api { image: "nginx:1.25" }'

MEDIUM = "\n".join(
    f'service svc{i} {{ image: "app{i}:1.0" replicas: {(i % 4) + 1} '
    f'port: {8000 + i} health http("/health") }}'
    for i in range(20)
)

LARGE = "\n".join(
    f'service svc{i} {{ image: "app{i}:1.0" replicas: {(i % 4) + 1} '
    f'port: {8000 + i} health http("/health") '
    "resources { requests { cpu: 100m, memory: 128Mi } "
    "limits { cpu: 500m, memory: 256Mi } } }"
    for i in range(100)
) + "\n" + "\n".join(
    f"database db{i} {{ type: postgres storage: 10Gi ssl: true }}" for i in range(20)
)

BACKENDS = ["kubernetes", "compose", "github", "terraform"]


def _ms(fn, n: int = 5) -> float:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return sum(times) / len(times)


def _mem(source: str) -> int:
    tracemalloc.start()
    parse(source)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak  # bytes


def benchmark() -> dict:
    results: dict = {}

    results["parse_small_ms"] = _ms(lambda: parse(SMALL), 20)
    results["parse_medium_ms"] = _ms(lambda: parse(MEDIUM), 10)
    results["parse_large_ms"] = _ms(lambda: parse(LARGE), 5)
    results["memory_large_bytes"] = _mem(LARGE)

    prog_large = parse(LARGE)
    for backend in BACKENDS:
        results[f"compile_{backend}_ms"] = _ms(
            lambda b=backend: get_backend(b).compile(prog_large), 3
        )

    # LSP completion latency (median over the same request)
    from infra.lsp.completion import completions_at

    src = "service api {\n    \n}"
    t0 = time.perf_counter()
    for _ in range(50):
        completions_at(src, 1, 4)
    results["lsp_completion_ms"] = (time.perf_counter() - t0) * 1000 / 50

    return results


def _fmt_row(key: str, val) -> str:
    if "bytes" in key:
        return f"{key:24} {val/1024:8.1f} KiB"
    if key.endswith("_ms"):
        return f"{key:24} {val:8.2f} ms"
    return f"{key:24} {val!r}"


#: guardrail factors vs the saved baseline
_TIME_FACTOR = 2.0  # allow up to 2x for timing (machine-dependent)
_MEM_FACTOR = 1.5  # allow up to 1.5x for memory


def compare_with_baseline(results: dict, baseline_path: Path) -> int:
    """Compare current results with the saved baseline; warn on regressions.

    Returns a nonzero count of regressions (so a caller can fail).
    """
    if not baseline_path.exists():
        print(f"\nNo baseline at {baseline_path}; run with --save first.")
        return 0
    baseline = {}
    for line in baseline_path.read_text().splitlines():
        if line.startswith("| ") and "Metric" not in line and "---" not in line:
            parts = [p.strip() for p in line.strip("| ").split("|")]
            if len(parts) >= 2:
                baseline[parts[0]] = parts[1]

    regressions = 0
    print("\n=== Regression check vs baseline ===")
    for key, current in sorted(results.items()):
        if key not in baseline:
            continue
        base_raw = baseline[key]
        try:
            base_val = float(base_raw.split()[0])
        except ValueError:
            continue
        if "bytes" in key:
            # baseline is stored in KiB; current is bytes
            base_bytes = base_val * 1024
            factor = current / max(base_bytes, 1)
            ok = factor <= _MEM_FACTOR
        else:
            factor = current / max(base_val, 1e-9)
            ok = factor <= _TIME_FACTOR
        status = "ok" if ok else "REGRESSION"
        if not ok:
            regressions += 1
        print(f"  {key:24} {base_raw:>10} -> {current:8.1f} ({factor:5.2f}x) {status}")
    print(f"Regressions: {regressions}")
    return regressions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="write baseline file")
    ap.add_argument("--compare", action="store_true", help="compare with baseline")
    args = ap.parse_args()

    results = benchmark()

    lines = ["# Benchmark baseline (v0.1.0)", ""]
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    for key in sorted(results):
        val = results[key]
        if "bytes" in key:
            lines.append(f"| {key} | {val/1024:.1f} KiB |")
        elif key.endswith("_ms"):
            lines.append(f"| {key} | {val:.2f} ms |")
        else:
            lines.append(f"| {key} | {val} |")
    lines.append("")
    lines.append("Measured with `python scripts/benchmark.py`.")

    text = "\n".join(lines)
    print(text)

    if args.save:
        Path("docs/benchmark_baseline.md").write_text(text + "\n")
        print("\nSaved docs/benchmark_baseline.md")
    if args.compare:
        regressions = compare_with_baseline(results, Path("docs/benchmark_baseline.md"))
        if regressions:
            print("\nPerformance regression(s) detected vs baseline.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
