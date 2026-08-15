# Benchmark baseline (v0.1.0)

## Running

```bash
python scripts/benchmark.py            # run and print
python scripts/benchmark.py --save     # run and update this baseline
python scripts/benchmark.py --compare  # run and flag regressions (>2x time / >1.5x memory)
```

## Baseline

| Metric | Value |
|--------|-------|
| compile_compose_ms | 79.62 ms |
| compile_github_ms | 1.34 ms |
| compile_kubernetes_ms | 496.01 ms |
| compile_terraform_ms | 0.12 ms |
| lsp_completion_ms | 0.11 ms |
| memory_large_bytes | 3549.9 KiB |
| parse_large_ms | 201.16 ms |
| parse_medium_ms | 27.16 ms |
| parse_small_ms | 44.43 ms |

Measured with `python scripts/benchmark.py`.
