# Known Limitations

This document honestly describes the boundaries of Infra Lang as of v0.1.0.
It is not a list of shame — it is a map of where the system stops, so users
know what to expect.

## Backend limitations

- **Terraform output is structural only.** It emits basic resources for
  `cluster`/`database`/`storage`/`network`/`secret`/`queue`, but no modules,
  data sources, or remote-state wiring. `service`/`cache`/`config` are accepted
  by the parser but produce **no** Terraform resources.
- **GitHub Actions** does not support reusable workflows (`workflow_call`) or
  matrix `include`-level features beyond the basics.
- **Kubernetes** does not emit `pipeline` or `cluster` structures (they are
  backend-specific to GitHub / Terraform respectively).
- **Compose** `storage` only maps the `minio` type; other storage types are
  not emitted.

## LSP limitations

- Diagnostics, completion, hover, document symbols, go-to-definition,
  find-references, formatting and quick-fixes are available.
- **No rename symbol**, **no cross-file navigation** (imports are not resolved
  across files for LSP features yet).
- Completion is heuristic and may offer fields that the strict parser later
  rejects for an incomplete block.

## Real-world E2E

- Generated Kubernetes YAML passes **schema validation** (kubeconform against
  the official schemas) for every example, but has **not** been verified with a
  live `kubectl apply` against a running cluster in CI (requires Docker/kind).
- No automated deployment smoke tests against a real cluster.

## Output directory behavior

- `infra-out/` accumulates artifacts across compiles; it is never auto-cleared
  (the compiler only writes files, never deletes them). Use separate output
  dirs per target or `rm -rf infra-out` for a clean comparison.

## Telemetry / feedback

- Feedback is **opt-in and off by default**. The collector endpoint is not yet
  configured/operational; enabling feedback is a no-op until a collector URL
  is wired up.
- Fingerprinting is by error class; it cannot yet report per-version/per-target
  breakdowns with full fidelity.

## Deliberately out of scope

- A general-purpose programming language (loops, functions over infra).
- A full replacement for Helm / Pulumi / Terraform.
- Kubernetes operator generation.
- A runtime engine / VM for infrastructure.
