# Changelog

All notable changes to Infra Lang are documented here.

## [0.2.0] - in development

### Added (implemented)
- LSP code actions (quick fixes) for safe, automatic fixes (e.g. E011
  `replicas: 0` → `replicas: 1`, E012 port out of range → valid port).
- LSP document symbols (outline of top-level blocks).
- LSP go-to-definition for block references in the same file.
- LSP find-references (single-file).
- LSP document formatting (via the existing `infra fmt` printer).
- Completion polish: ranking (sort_text), prefix filtering, and **symbol-aware**
  suggestions for reference fields (`depends`, `allow_from`, `allow_egress`).
- `infra feedback` CLI command to show/enable/disable the opt-in reporting.
- Feedback telemetry hardening:
  - stable error **fingerprinting** (hash of error class, never raw source),
  - hardened sanitization (also collapses line/column numbers),
  - precedence documented: env > project config > user config > defaults.
- Issue-triage readiness: richer bug/feature/parser templates with categories,
  reproducibility, and a labels manifest (`.github/labels.yml`).
- LSP context-aware completion:
  - top-level block keywords with snippet expansion
  - per-block field suggestions (service, database, cache, queue, etc.)
  - enum / bool / quantity value hints after `:`
  - sub-block suggestions (`resources`, `ingress`, `backup`, ...)
  - tolerant of incomplete / malformed input while typing
- Expanded LSP hover docs to cover all `service` and `database` fields plus
  popular keywords (46 entries total).
- Opt-in anonymous error reporting (feedback) infrastructure:
  - disabled by default, local config (`<project>/.infra-config.yaml` or
    `~/.config/infra/config.yaml`), env-var override
  - never sends source code, file paths, or PII
  - a collector/network failure never breaks CLI or LSP

### Will add (confirmed)
- Go to definition for imported constants
- Richer hover docs (full block documentation)
- Real cluster validation helpers (`kind`/`minikube` commands)
- Terraform output improvements

### Will add (if users ask)
- Drift detection
- Cost estimation
- Additional providers/backends
- Plugin system

### Will NOT add (decided)
- General purpose programming language features
- Full replacement for Helm/Pulumi/Terraform
- Kubernetes operator generation
- Runtime engine / VM

## [0.1.0] - 2026-08-15

### Added
- Core DSL with LALR(1) grammar — 11 top-level structures:
  `service`, `database`, `cache`, `queue`, `storage`, `network`, `secret`,
  `config`, `pipeline`, `environment`, `cluster`.
- 4 compilation backends: Kubernetes, Docker Compose, Terraform HCL,
  GitHub Actions.
- Security linter: SEC001–SEC010 (10 rules).
- Reliability linter: REL001–REL014 (14 rules).
- Kubernetes resources: Deployment, Service, Ingress, StatefulSet, PVC,
  Secret, ConfigMap, CronJob, HPA, PodDisruptionBudget, NetworkPolicy,
  ResourceQuota, Namespace, ServiceAccount, ClusterRole,
  ClusterRoleBinding, TopologySpreadConstraints.
- `schedule` blocks → CronJobs + HPA + RBAC.
- `autoscale` block → HorizontalPodAutoscaler.
- `disruption` block → PodDisruptionBudget.
- Inline `network_policy` → NetworkPolicy; `topology` →
  TopologySpreadConstraints; `affinity`/anti-affinity → pod scheduling.
- `environment.quotas` → ResourceQuota.
- `environment.extends` → inheritance resolver.
- Import system with cycle detection; `extends` for services/environments.
- Template-string interpolation (backticks) with `{expr}`.
- CLI: `compile`, `validate`, `fmt`, `diff`, `graph`, `docs`, `repl`,
  `init`, `check`.
- `--var` CLI variable injection.
- `--watch` live recompilation.
- `--validate-output` Kubernetes structure validation.
- Idempotent formatter.
- AST-based diff engine.
- Interactive REPL.
- VS Code extension (`vscode-infra-lang/`): syntax highlighting + 12 snippets.
- GitHub Actions workflows (`ci.yml`, `publish.yml`).
- Tutorial (`docs/tutorial.md`), demo project (`examples/demo/`),
  language spec, release notes and publishing checklist.

### Fixed
- LSP completion crashed with `IndexError: no such group` when given a block
  without a name (e.g. `service {`); the anonymous-block regex used a
  non-capturing group. Fixed and covered by a chaos/storm regression test.
- `infra docs` now renders `secret` / `config` / `network` / `environment` /
  `cluster` as their user-facing names instead of the raw AST class names
  (`secretdef`, `configdef`, ...).
- Documented that `infra-out/` accumulates artifacts across compiles and is
  never auto-cleared; suggested separate output dirs per target.
- Pinned `pygls` to `<2.0` (the code uses the 1.x API; 2.x moved
  `LanguageServer` to `pygls.lsp.server` and renamed `publish_diagnostics`).
- `test_clean_venv_install` now locates venv scripts cross-platform
  (`Scripts/` on Windows, `bin/` on POSIX) instead of assuming a Unix path.
- `test_watch_recompiles_on_change` now reads watch output stream-wise so it
  works even when `rich` buffers non-TTY output and the process is terminated.
- Property-based tests now set `deadline=5000` so slow CI machines don't hit
  the default 200ms hypothesis deadline.
- 5× `'Map' object is not iterable` in the transformer
  (network-policy `selector`, stage `env`, node-pool `labels`, IAM `policy`,
  build `args`).
- `canary:` strategy never built a `CanaryStep` (was a Token).
- `rate_limit`/`cors` blocks without a colon were silently dropped.
- camelCase vs snake_case key mismatch (`restoreKeys`, `cancelInProgress`,
  `runsOn`) meant fields never populated.
- Missing `sa_item`/`role_item` transformer methods left IAM fields empty.
- `health http("/x") { path: "/" }` crashed with a duplicate `path` kwarg.
- `.name` used on a Token in `queue_config_item`/`env_from_entry`.
- `port:` inside a health block was mis-routed to a `ports` tuple.
- `_apply_topology` ran after `_clean_none`, dropping topology from output.
- `disruption { min_available: 50% }` crashed the Kubernetes backend
  (Percentage object) — now rendered as `"50%"`.
- `health: http("/")` (colon form) did not parse.
- Unparseable files dumped a raw rich traceback instead of a clean
  `error[PARSE]` message.
- `infra docs` leaked prelude built-in constants into the inventory.
- Standardized `app.kubernetes.io/managed-by` label to `infra-lang` (was
  inconsistently `infra` in some resources).
- `strategy: rolling` now generates `RollingUpdate` (was silently omitted).
- `bool(Literal(False))` resolved to `True` incorrectly, reversing semantics
  of `privileged: false` and `readOnlyRootFilesystem: false`.
- Docker Compose top-level `volumes:` section was missing for named volumes.
- Thread-unsafe shared `YAML()` instance in `base.py` caused `EmitterError`
  during parallel compilation (affected LSP, `--watch`, and multi-file
  compile).
- `pyyaml` was used by `config`, `k8s_validator`, and `schema_validator` but
  was not declared as a runtime dependency — a clean install would break
  `--validate-output` and config. Added `pyyaml>=6.0` and a regression test.

### Notes
- First public release.
- Requires Python 3.11+.
