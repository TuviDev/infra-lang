# Infra Lang v0.1.0 (private) — Release Notes

**Release date:** 2026-08-15 · **Version:** 0.1.0 · **Tag:** `v0.1.0-private`

Infra Lang is an Infrastructure-as-Code DSL that compiles a single `.infra`
file to Kubernetes YAML, Docker Compose, Terraform HCL, and GitHub Actions.

## Highlights

- **One definition, four backends** — `service`, `database`, `cache`, `queue`,
  `storage`, `network`, `secret`, `config`, `pipeline`, `environment`,
  `cluster`.
- **Kubernetes** emits 17 resource types (Deployment, Service, Ingress,
  StatefulSet, PVC, Secret, ConfigMap, CronJob, HPA, PodDisruptionBudget,
  NetworkPolicy, ResourceQuota, Namespace, ServiceAccount, ClusterRole,
  ClusterRoleBinding, TopologySpreadConstraints).
- **Built-in linters** — 10 security rules (SEC001–SEC010) and 13 reliability
  rules (REL001–REL014) with actionable hints; `Error`-severity findings block
  compilation.
- **LSP server** — diagnostics, hover, context-aware completion, document
  symbols, go-to-definition, find-references, formatting, and quick-fixes.
- **VS Code extension** — syntax highlighting, 12 snippets, and LSP client.
- **Opt-in telemetry** — anonymous error reporting, off by default.

## Quality

- 1584 tests, ~93% coverage, ruff clean, mypy clean.
- Generated Kubernetes YAML passes `kubeconform -strict` against the official
  schemas (all public examples: 0 invalid).
- Clean wheel install verified in a fresh virtualenv.

## Installation

```bash
pip install infra-lang
pip install 'infra-lang[lsp]'   # with LSP support
```

## Notes

- `examples/04_cicd_pipeline.infra` is a **GitHub-target-only** example; no
  Kubernetes output is expected for it.
- `infra-out/` accumulates artifacts across compiles and is never auto-cleared;
  use separate `--output` dirs per target for clean comparisons.
- On Windows, one watch-mode recompile test is skipped (a `watchdog`/
  `tmp_path` limitation, not a product bug).

## Known limitations

See [docs/known_limitations.md](known_limitations.md) for the honest boundaries
(structural Terraform output, no live-cluster CI, single-file LSP navigation).

## What's next (v0.2.0)

Cross-file LSP navigation, rename symbol, real-cluster validation helpers, and
Terraform output improvements. See [docs/roadmap_v0.2.0.md](roadmap_v0.2.0.md).
