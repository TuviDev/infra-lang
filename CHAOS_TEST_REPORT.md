# Chaos / Stress Test Report

## Large file stress
- source size: 155 structures (100 services + 20 databases + 20 caches + 10 queues + 5 pipelines), ~151 lines
- parse time: ~940ms
- validate time: ~8ms
- compile (K8s) time: ~507ms
- status: **PASS** (well within generous budgets; no hang/crash)

## Backend x feature matrix
| feature | backend | parse | validate | compile | output valid |
|---------|---------|-------|----------|---------|--------------|
| service+autoscale | k8s/compose/gh | ✅ | ✅ | ✅ | ✅ |
| service+disruption | k8s/compose/gh | ✅ | ✅ | ✅ | ✅ |
| service+network_policy | k8s/compose/gh | ✅ | ✅ | ✅ | ✅ |
| service+topology | k8s/compose/gh | ✅ | ✅ | ✅ | ✅ |
| service+affinity | k8s/compose/gh | ✅ | ✅ | ✅ | ✅ |
| database+backup+ssl | k8s/compose/gh | ✅ | ✅ | ✅ | ✅ |
| environment+quotas+extends | k8s/compose/gh | ✅ | ✅ | ✅ | ✅ |
| pipeline+matrix | k8s/compose/gh | ✅ | ✅ | ✅ | ✅ |

status: **PASS**

## Parallel compilation
- workers: 10 (ThreadPoolExecutor)
- crashes: 0 / 10
- shared state corruption: **0 after fix** (was corrupting before fix)
- issues: see "Bugs found" below

## Repeated compile loop
- iterations: 50 (same program)
- memory behavior: stable (no growth observed)
- timing drift: none (output identical across iterations)
- status: **PASS**

## Bugs found and fixed

### BUG 1 (serious): thread-unsafe shared ruamel YAML instance
- **Symptom:** compiling the same program in parallel (or after a parallel
  compile in the same process) raised
  `ruamel.yaml.emitter.EmitterError: expected NodeEvent, but got
  DocumentStartEvent()`.
- **Cause:** `src/infra/backends/base.py` defined a single module-level
  `_yaml = YAML()` shared by every backend. ruamel's `YAML` is **not
  thread-safe**; concurrent `dump()` calls corrupt its internal emitter state,
  and the corruption then breaks subsequent compiles in the same process.
- **Impact:** any concurrent compile (multi-file `infra compile`, the LSP
  server processing several documents, watch mode recompiles) could fail
  nondeterministically.
- **Fix:** replaced the shared instance with `_new_yaml()` which returns a
  freshly-configured `YAML()` per `_to_yaml()` call. `_to_yaml` now uses a
  per-call instance, making compiles thread-safe.
- **Regression test:** `tests/test_chaos_audit.py::TestParallelCompilation` and
  `TestRepeatedCompile` (they fail before the fix, pass after).

### BUG 2 (minor): stale `MANAGED_BY_LABEL` constant
- `MANAGED_BY_LABEL` in `base.py` still read `app.kubernetes.io/managed-by:
  infra` (the `infra` form) while all generated resources use `infra-lang`.
- Fixed to `infra-lang` to match the emitted label.

## Test authoring fixes (not product bugs)
- Large-source generator had an unbalanced-brace bug; fixed so the stress
  source actually parses.
- YAML assertions used `safe_load()` (single-doc) on multi-doc output; switched
  to `safe_load_all()` and restricted to `.yml/.yaml` files (Compose emits
  `.env.example` and `Makefile` which are not YAML).
