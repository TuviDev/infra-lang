# Changelog

All notable changes to Infra Lang are documented here.

## [0.3.1] - 2026-08-20

### Performance
- **Reused the ruamel.yaml emitter per thread** instead of constructing a new
  `YAML()` instance for every resource during compilation. Compiling a large
  chart (500 services + 100 databases) is now ~2–3× faster for the Kubernetes
  backend (6.0s → ~3.0s) and ~1.5× faster for Helm (1.5s → ~1.0s). The reuse is
  thread-safe via a `threading.local()` cache, so multi-file / LSP / watch-mode
  concurrent compiles remain safe.

### Security & robustness
- The anonymous feedback reporter now validates the collector URL scheme
  (`http`/`https` only) before sending, and keeps a 2s `urlopen` timeout.
- Verified `ImportCycleError` inherits `InfraError` and is reported by the CLI
  as a consistent `error[PARSE]` (graceful, not a crash).

### Code quality
- Reduced `mypy --strict` findings from 58 to ~42 by adding generic type
  arguments (`dict[str, list[TextEdit]]`, `Dict[str, Dict[str, Any]]`, etc.)
  across the LSP server and CLI graph modules.
- Fixed a latent LSP bug: the single-document references fallback returned
  `Range` objects instead of `Location` (it now wraps them with the document
  URI, matching the LSP references protocol).

### Tests
- Added a Windows `file:///C:/...` URI conversion regression test (the
  `url2pathname` contract for the leading-slash drive form).
- Added 16 transformer AST tests (service/environment `extends`, port
  host:target, `envFrom`, affinity, strategy, security, lifecycle, health exec,
  topology, disruption, autoscale, network policy) to raise branch coverage.

## [0.3.0] - 2026-08-20

### Added
- **Helm `values.schema.json`** — the Helm backend now emits a JSON Schema
  (Draft-07) alongside `values.yaml` that validates the chart's configurable
  values (service/secret/configmap structure, workload kinds, image shapes).
  `helm lint --strict` passes on generated charts with the schema present.
- **VS Code Marketplace / Open VSX automation** — added `publish:marketplace`
  and `publish:openvsx` npm scripts, the `ovsx` dev dependency, and a
  `.github/workflows/marketplace.yml` that publishes the extension to both
  registries on version tags (via `VSCE_PAT` / `OVSX_TOKEN` secrets).
- **Friendlier parser hints** — two more common syntax errors now get helpful
  messages: a missing colon after a field name (`Expected ':' after field name
  'image'. Did you forget the colon?`) and an unterminated string literal.

### Fixed
- **Windows CI UTF-8 file encoding** — every `write_text()`/`open()` call across
  the CLI now passes an explicit `encoding="utf-8"`. Previously files such as
  the generated Helm `Chart.yaml` / `values.yaml` were written with the Windows
  default code page (cp1252), producing `yaml: invalid leading UTF-8 octet` when
  `helm` re-read them.
- **Docker daemon probe** — `have_docker()` reports `False` when the daemon does
  not respond (short timeout, Windows/macOS CI), so live Compose E2E correctly
  skips instead of failing when Docker isn't actually usable.

## [0.2.0] - 2026-08-20

### Added
- **`infra doctor --check-drift`** — detects on-disk drift in generated output.
  Recompiles a `.infra` file for a target and compares it against generated
  files (`--out-dir`, default `infra-out`), reporting modified/missing files as
  unified diffs. Exit code 0 when clean, 1 on drift. Addresses post-launch
  feedback that users hand-edit generated manifests, silently diverging from
  the `.infra` source of truth.
- **`--json` output flag** for `infra validate` and `infra doctor`, for CI/CD
  integration:
  - `infra validate <file.infra> --json` → `{valid, file, errors[], warnings[]}`
    with per-finding `severity` and location.
  - `infra doctor --json` → structured tool/environment report.
  - `infra doctor --check-drift <file> --json` → `{has_drift, modified_files[],
    missing_files[]}`.
- **VS Code extension Marketplace readiness** — added full `package.json`
  metadata (publisher, license, repository, homepage, bugs, keywords,
  categories, icon), a dedicated `vscode-infra-lang/README.md`, a `.vsix`
  packaging script via `@vscode/vsce`, an `icon.png`, and an
  `.github/workflows/extension.yml` that builds and uploads the `.vsix`.
- **Mutation-hardening test suites** — ~40 new contract/boundary tests across
  the Terraform, Kubernetes, Compose and security-linter backends (provider
  combinations, missing optional fields, probe thresholds, multi-port services,
  RBAC/CronJob, base64 secrets, multiple simultaneous SEC findings, and
  Error-severity blocking compile).

### Fixed
- Parser now preserves the source filename when the cached prelude is loaded
  (the prelude re-parse was clobbering the current-file name used by the
  AUTO-GENERATED output header, making no-drift comparisons nondeterministic).
- VS Code extension `engines.vscode` aligned with `@types/vscode` so
  `vsce package` accepts the build.

## [0.1.1] - 2026-08-20

### Added
- **`infra import`** — reverse-compiles existing Kubernetes YAML back into
  readable Infra source. Supports Deployments, StatefulSets, Services, Secrets,
  ConfigMaps and Ingresses, groups a Service matching a Deployment's pod labels
  into one `service` block, maps postgres/mysql/mongo StatefulSets to
  `database` blocks and redis to `cache`, and reads multi-document YAML or whole
  directories (`infra import manifests/`). Output goes to stdout by default or
  to a file with `--output`.
- **`infra doctor`** — checks the local environment (Python version, Docker,
  kubectl, helm, kind, kubeconform, LSP/pygls) and reports what's installed or
  missing.

### Fixed
- Parser now strips UTF-8 BOM from input files (Windows editors compatibility).
  Previously a file saved by Notepad / `Out-File` with a UTF-8 BOM failed with
  `InfraLexError: Unexpected character '\ufeff'`.
- Friendlier parse error messages for the three most common mistakes: a missing
  closing brace ("Missing closing brace. Did you forget to close the block
  started at line X?"), an unknown keyword (with a "did you mean" suggestion),
  and a field missing its value ("Expected a value after 'image:'."). The same
  messages flow through to LSP diagnostics.

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
- **Neovim setup guide** (`docs/editors/neovim.md`): copy-paste LSP
  configuration for the built-in Neovim client — nvim-lspconfig and vanilla
  variants, filetype detection, semantic-token-driven highlighting, and
  troubleshooting.
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
