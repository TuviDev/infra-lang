# Changelog

All notable changes to Infra Lang are documented here.

## [0.1.0] - 2026-08-16

Initial public release.

### Added

**Language**
- 11 top-level resource types: `service`, `database`, `cache`, `queue`,
  `storage`, `network`, `secret`, `config`, `pipeline`, `environment`,
  `cluster`.
- A hand-written LALR(1) grammar with `{}` blocks (no YAML).
- Semantic validation with 30+ error codes, source locations, and actionable
  hints.
- Template-string interpolation, `import` with cycle detection, and `extends`
  inheritance.
- 25+ stdlib functions and a prelude of reusable constants.
- Time-aware scaling (`schedule` → CronJobs + HPA + RBAC), autoscaling
  (`autoscale` → HPA), disruption budgets (`disruption` → PDB), affinity and
  topology spread, network policies, and per-environment quotas.

**Backends**
- **Kubernetes** — emits 17 resource kinds (Deployment, Service, Ingress,
  StatefulSet, PVC, ConfigMap, Secret, CronJob, HPA, PodDisruptionBudget,
  NetworkPolicy, ResourceQuota, Namespace, ServiceAccount, ClusterRole,
  ClusterRoleBinding, TopologySpreadConstraints).
- **Docker Compose** — `docker-compose.yml`, `.env.example`, `Makefile`.
- **Terraform** — `main.tf`, `variables.tf`, `outputs.tf`, `providers.tf`
  (AWS/GCP/Azure).
- **GitHub Actions** — `.github/workflows/*.yml` + `dependabot.yml`.

**Quality gates**
- Security linter (SEC001–SEC010): hardcoded secrets, credential patterns,
  mutable image tags, privileged containers, and more.
- Reliability linter (REL001–REL014): thundering herd, even HA replicas, no
  memory limits, missing backups, single-replica Kafka, and more.
- `Error`-severity findings block compilation.

**Tooling**
- CLI: `compile`, `validate`, `fmt`, `repl`, `init`, `check`, `graph`, `docs`,
  `diff`, `lsp`, `feedback`.
- Formatter (`infra fmt`), REPL (`infra repl`), and an AST diff engine
  (`infra diff`).
- Language server: diagnostics, hover, context-aware completion (46+ fields),
  document symbols, go-to-definition, find-references, workspace symbols,
  symbol rename, formatting, and quick-fixes.
- **Whole-project indexing**: on startup the server scans the workspace root
  for `*.infra` files (non-blocking, bounded, tolerant of malformed files) so
  navigation works across every file on disk, not just open tabs.
- VS Code extension with syntax highlighting, snippets, and an LSP client.
- **Cross-file rename**: renaming a symbol now propagates to every file in the
  project, including files on disk not open in the editor (via the workspace
  index); word boundaries are respected (`-`/`_` are part of an identifier).
- **Semantic tokens**: precise LSP syntax highlighting (block keywords, resource
  names, field names, type values, strings, numbers, comments) via
  `textDocument/semanticTokens/full`; tolerant of malformed input.
- **Diagnostics with context**: every diagnostic carries a code, `source:
  "infra-lang"`, and a clickable docs link; duplicate-name errors include
  related information pointing at the earlier definition.
- **Signature help**: shows the fields available inside the current block with
  types and docs (triggered by `{`, newline, `.`); used fields marked `(set)`.
- **Document highlight**: highlights every occurrence of the symbol under the
  cursor (Write for definitions, Read for references), word-boundary aware.
- **Folding ranges**: foldable `{}` blocks (top-level and nested) and comment
  runs.
- **Helm backend** (`-t helm`): compiles to a complete, idiomatic Helm chart
  (`Chart.yaml`, `values.yaml`, `templates/`, `_helpers.tpl`, `.helmignore`)
  that passes `helm lint --strict` and renders with `helm template`. Maps
  `service`/`cache` → Deployment, `database`/`queue` → StatefulSet (+PVC),
  `secret` → base64 Secret, `config` → ConfigMap; all parameters configurable
  via `values.yaml`; multi-port services get `tcp-<port>` names.
- Opt-in anonymous error reporting (off by default; never sends source code,
  paths, or PII).

**Tests**
- 1758 tests across lexer, parser, transformer, analyzer, backends, CLI, LSP
  (incl. 55 Helm unit + live `helm lint`/`template` tests).
- **Live Helm E2E** (`pytest -m live_e2e`): runs `helm lint --strict` and
  `helm template` on every example's generated chart; skipped when helm is
  absent.
- **Live Kubernetes E2E** (`pytest -m live_e2e`): compiles the examples and
  really applies them to a `kind` cluster with `kubectl`, verifying Secret
  base64, multi-port Service names, and labels. Automatically skipped when the
  tools are missing.
- **Live Compose E2E** (`pytest -m live_e2e`): compiles examples to Docker
  Compose and really runs `docker compose config` (all service examples) and
  `docker compose up -d --wait` (examples with only public images), then
  `down -v` cleans up. Skipped without a Docker daemon. Includes regression
  guards (multi-port, secret declaration + mounting) that run in the normal
  suite.

### Fixed
- **Compose**: a service using `from secret "x.y"` now mounts the secret into
  the container (`secrets:` on the service). Previously the secret was declared
  top-level but never mounted, so it was unreachable at runtime.
- Secrets now emit valid base64 in `data:` (was `illegal base64 data` on
  `kubectl apply`).
- Multi-port Services (including the RabbitMQ queue path) now get named ports,
  required by the Kubernetes API.
- Standalone `secret`/`config` resources now carry the `managed-by` label like
  every other resource.
- LSP crashed with `IndexError` when the editor reported a cursor past the end
  of a short line; positions are now clamped.
- LSP `did_close` restores the on-disk file state on Windows (path conversion
  via `url2pathname`).
- Removed the deprecated top-level `version:` key from Compose output.
- Bumped `upload-artifact`/`download-artifact` from deprecated `@v3` to `@v4`.
- Fixed several `mypy --check-untyped-defs` findings (type annotations, watch
  mode byte-path handling).

### Upcoming (planned)
- `kind`/`minikube` helper commands (`infra up`, `infra verify`).
- Terraform modules and more explicit outputs.
- GitHub reusable workflows (`workflow_call`).
- Richer LSP hover and cross-file rename.
- A plugin system (based on community feedback).

### Explicitly out of scope
- General-purpose programming language features.
- Full replacement for Helm / Pulumi / Terraform.
- Kubernetes operator generation.
- A runtime engine / VM.

---

See [docs/release_notes_v0.1.0.md](docs/release_notes_v0.1.0.md) for the
release notes.
