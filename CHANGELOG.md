# Changelog

All notable changes to Infra Lang are documented here.

## [0.5.5] - 2026-08-30

**Block A: Use-Cases** — side-by-side environment comparison and a
shareable SVG export of the architecture DAG. No DSL grammar changes, no
new runtime dependencies.

### Added
- **Side-by-side environment comparison** —
  `infra serve app.infra --compare <env_a> <env_b>` (and the `infra ui`
  alias) serves a static compare page on the loopback server, or writes it
  with `-o compare.html` (`[OK] Compare report written:`). The single-file
  report contains a diff table (`+` added, `−` removed, `Δ` changed at
  field level: replicas, image, ports, expose, storage, env vars,
  resources, plus a FinOps delta row) and two panels with per-side
  workload tables and monthly cost estimates. The special name `base`
  selects the unoverlaid file; overlays are applied with
  `apply_environment_overlay`, and the `program.environments` list is
  restored after each overlay (same quirk handling as the dashboard).
  Error states are explicit: unknown overlay name exits 1 listing the
  available environments, `--compare` cannot be combined with
  `-e/--environment`, and identical environments render a readable
  "No differences" empty state.
- **Architecture DAG export to SVG** — `infra graph app.infra -o out.svg`
  (format inferred from the `.svg` suffix) or `--format svg` writes a
  self-contained SVG document using the same DAG collector and
  longest-path layout as the dashboard; nodes/edges carry
  `data-name`/`data-from`/`data-to` attributes. Requires exactly one input
  file and supports `-e/--env`. The dashboard's Architecture tab gains a
  **Download SVG** button embedding exactly the same document as a data
  URI. `infra graph` also gained `-o` (short for `--output`) and
  `-e/--env` for all formats.
- **PNG export consciously deferred** (candidate for 0.5.7 if a cheap,
  pure-Python path materializes) — rasterizing SVG requires either
  `cairosvg` (native cairo) or a browser driver (playwright/chromium),
  both ruled out by the zero-native-deps policy for this line. PDF export
  stays out of scope.

## [0.5.4] - 2026-08-30

**Quality & CI Foundation release** — no DSL grammar or dashboard/UI changes.
Consolidates the post-0.5.3 hotfix line and hardens tests, CI and docs ahead
of the 0.5.5 feature track.

### Fixed
- **`infra serve` port conflict now fails fast and correctly on Windows** —
  `_DashboardHTTPServer` no longer sets `SO_REUSEADDR` (Windows reuse semantics
  allowed a silent port hijack, so a busy port did not raise `EADDRINUSE` and
  the CLI hung in `serve_forever` instead of exiting 1). The port-conflict
  test also pins the blocker with `SO_EXCLUSIVEADDRUSE` on win32.
- **Idempotent PyPI publish** — `publish.yml` now uploads with
  `twine upload --skip-existing`, so re-tagging the same version (or a retried
  publish run) stays green instead of HTTP 400.

### Changed
- **CI profiles** — Windows runs a sequential smoke profile
  (`pytest -q -o addopts="" -m "not slow"`, no xdist/coverage) after diagnosing
  xdist+coverage deadlocks on Windows pipe buffers / file locks; Unix keeps
  parallel full runs with the coverage gate. New `nightly.yml` workflow
  (schedule + `workflow_dispatch`) runs the FULL suite incl. the `slow`
  sections on all 3 OSes; every job now has `timeout-minutes` (15 PR, 45
  nightly).
- **Test suite hygiene** — `pytest-timeout` (60 s global, 300 s on `slow`
  classes), `slow` markers on the chaos/CLI-subprocess/packaging groups,
  idempotence loop trimmed 50→10 iterations, 2 new public-API edge tests
  (`infra.parse_file`, `infra.compile(source: str)`) lifting
  `src/infra/__init__.py` to 100% line coverage.
- **Docs** — CONTRIBUTING gained a “Running tests on Windows vs Unix” section;
  intent comments added to previously bare `pass` sites
  (`compile.py`, `init.py`, `serve_cmd.py`, `feedback.py`).
- **pytest config** — `addopts` no longer forces xdist by default (CI passes
  `-n auto` explicitly), so local/Windows runs are sequential out of the box.

### Internal
- Foundation audit report committed as `FOUNDATION_AUDIT.md` (baseline numbers,
  TOP-30 slow map, dependency and security review, P0/P1/P2 backlog).

## [0.5.3] - 2026-08-27

### Fixed
- **CI dual-stack LSP tests** — `test_returns_placeholder_for_block` is now
  skipped with a precise reason on the pygls 1.3.x / lsprotocol 2023.x stack
  (where `PrepareRenamePlaceholder` does not exist and the handler's plain
  `Range` fallback applies), so the full suite passes on both stacks.
- **CLI `--help` assertions** — `infra serve --help` output is stripped of
  ANSI escape codes before flag assertions (Rich may style help output on
  non-TTY CI runners).

### Changed
- **GitHub Actions upgraded to the Node 24 runtime** ahead of the Node 20
  removal from runners (2026-09-16): `actions/checkout@v5`,
  `actions/setup-python@v6`, `actions/setup-node@v5`,
  `actions/upload-artifact@v5`, `actions/upload-pages-artifact@v4`, and the
  extension toolchain pinned to **Node 22 LTS** (`node-version: "22"`).
- **Runtime dependencies gained upper bounds** — `lark<2.0.0`, `typer<1.0.0`,
  `rich<16.0.0`, `ruamel.yaml<1.0.0`, `pyyaml<7.0`, `watchdog<7.0.0`,
  `prompt_toolkit<4.0.0` — protecting installs from unreviewed future
  major-version breakages (all currently-latest releases satisfy the ranges).
- **Test coverage push** — 107 new edge-case tests; `cli/lsp_cmd.py`
  77%→100%, `analyzer/environments.py` 86%→99%, `diff/engine.py` 86%→100%,
  `cli/init.py` 87%→100%, `config.py` 87%→100%, `feedback.py` 88%→100%,
  `backends/helm.py` 89%→96%, `cli/printer.py` 89%→95%. Totals: LINE 97.6%,
  BRANCH 93.5%.
- **README** now documents the `infra serve` / `infra ui` dashboard section
  with command examples; `docs/roadmap_v0.2.0.md` and
  `docs/release_notes_v0.1.0.md` carry archival banners; a no-op branch in
  the GitHub Actions backend (`backends/github.py`) gained an explanatory
  comment.

## [0.5.2] - 2026-08-26

### Added
- **Interactive visual web dashboard** (`infra serve` / `infra ui`) — a
  local, standard-library-only HTTP server (`http.server` +
  `ThreadingMixIn`, zero web frameworks) that renders a live
  single-page dashboard for a `.infra` file at
  `http://localhost:<port>` (bound to `127.0.0.1` only; Ctrl+C stops it
  cleanly). Browser auto-open via `webbrowser` can be disabled with
  `--no-browser`; an environment overlay is applied with `-e/--env`.
- **Architecture DAG visualization** — inline-SVG graph of services,
  databases, caches and queues connected by `depends_on`/`depends`
  edges (longest-path layered layout with cycle guard; ghost "external"
  nodes for forward-referenced dependencies), plus a shared-infrastructure
  lane for networks, secret stores and network policies. Hovering a node
  highlights its connected edges.
- **FinOps calculator panel** — monthly cost table per resource (vCPU,
  RAM, storage), the estimated monthly total and a cost-share bar chart.
- **Drift panel** — renders an optional `DriftReport` with an
  In-Sync/Drifted badge and per-field expected-vs-live highlighting
  (escaped, so crafted values cannot inject markup); shows probe errors
  or a "data not collected" note when no report is supplied.
- **Environment preview switcher** — a `<select>` listing every declared
  environment (`environment prod { ... }` definitions and
  `environment "name" { service ... }` overlays with override counts);
  the `-e` flag preselects the active overlay, which is also shown in
  the page header.
- **Offline HTML report export** — `infra serve app.infra
  --output-html report.html` writes a fully standalone single-file
  report (inline CSS/JS, no external references, works offline and in
  sandboxed viewers) without starting the HTTP server; `infra ui` is an
  official alias of `infra serve`.

## [0.5.1] - 2026-08-24

### Added
- **Top-level `network_policy` declarations** — native network security
  policies directly in the DSL:
  `network_policy "app_sec" { target: "api", allow_ingress: ["frontend"],
  allow_egress: ["database"], block_all_ingress: true }`. The new AST node
  `NetworkPolicyDef` captures the target workload, the allowed ingress and
  egress peer lists and the blanket inbound block; `infra fmt`
  round-trips the block, document symbols / completion / hover in the
  language server recognize it, and the symbol table registers
  `network_policy` symbols (with `E002` duplicate detection).
  The pre-existing per-service `network_policy { allow_from: ... }`
  sub-block is untouched.
- **`POLICY_TARGET_NOT_FOUND` validation** — every workload referenced by
  `target`, `allow_ingress` or `allow_egress` must be declared in the
  file (services and resources alike; forward references are fine);
  dangling references are hard errors with a fix-it hint. Declaring
  `block_all_ingress` together with `allow_ingress` rules emits advisory
  warning `W012` (allow rules take precedence over the blanket block).
- **Network policy code generation in three backends** —
  Kubernetes emits a `networking.k8s.io/v1` `NetworkPolicy` per
  declaration: `podSelector` binds the target by the standard
  `app.kubernetes.io/name` label, peers become `ingress.from` /
  `egress.to` selectors, `block_all_ingress` with an empty allow-list
  renders `ingress: []` (deny-all), and `policyTypes` mirrors the
  declared rules. Docker Compose maps each policy to a dedicated bridge
  network (`np_<name>`) shared only by the target and its allowed peers —
  network membership is the isolation mechanism (a target with an
  explicit `networks:` list drops off the shared default network).
  Terraform generates the provider's security resource inline in
  `main.tf`: `aws_security_group` with allow blocks per peer (group
  default-deny implements the block), tag-based
  `google_compute_firewall` allow/deny pairs, or priority-ordered
  `azurerm_network_security_group` security rules (including the
  deny-all-inbound override at priority 4096). Programs without
  `network_policy` compile byte-identically to 0.5.0.

## [0.5.0] - 2026-08-24

### Added
- **`secret_store` declarations with ExternalSecrets integration** — a new
  top-level block declares an external secret backend once and reuses it
  across secrets: `secret_store "vault_store" { provider: "vault" ... }`
  with `provider: "vault" | "aws" | "gcp" | "kubernetes"` plus optional
  `address`, `path`, `region`, `namespace` and `project`. A `secret` block
  binds to it with `store: "vault_store"`; the validator raises
  `STORE_NOT_FOUND` (with a fix-it hint) when the reference dangles and
  `INVALID_STORE_PROVIDER` for unknown providers. The Kubernetes backend
  compiles stores to `SecretStore` manifests and bound secrets to
  `ExternalSecret` CRDs (`external-secrets.io/v1beta1`, `remoteRef.key`
  from the store path); Docker Compose emits `external: true` secrets
  (no literal values in `.env` files); Terraform generates the matching
  cloud resources — `aws_secretsmanager_secret`,
  `google_secret_manager_secret`, `vault_generic_secret` or
  `kubernetes_secret` — including provider blocks and
  `required_providers` entries where needed. Legacy inline secrets compile
  byte-identically to 0.4.5.
- **Generic custom resources (CRD plugin system)** — any Kubernetes CRD
  can now be declared directly in the DSL:
  `resource "custom_crd" "my_resource" { api_version: "...", kind: "MyKind",
  spec { ... } }`. Property values accept both `key: expression` and the
  bare-map form `key { ... }` (arbitrarily nestable); keys tolerate every
  DSL keyword, so real manifest fields such as `type`, `resources` or
  `spec` work unquoted. The validator emits advisory `W010`/`W011`
  notices when `api_version`/`kind` are missing and hard `E050` errors
  for duplicate properties at any nesting level. The Kubernetes backend
  renders the manifest verbatim (clean YAML with `apiVersion`, `kind`,
  `metadata` and the full `spec` tree), the Helm backend ships it under
  the chart's `crds/` directory (installed by Helm before all templates),
  and the Compose/Terraform backends report a clear skip notice in
  compilation warnings instead of silently dropping the declaration.
- **LSP completion for the new constructs** — the completion engine now
  suggests `depends_on` and `store` inside `service`/`secret` blocks,
  offers `secret_store` field hints with provider values
  (`vault`/`aws`/`gcp`/`kubernetes`), completes `resource` blocks with
  `api_version`/`kind`/`spec` fields and resolves `depends_on` / `store`
  values against symbols declared in the workspace. Hover documentation
  covers the new fields. Document symbols and go-to-definition recognize
  `secret_store` and `resource` blocks (quoted names included).

### Changed
- **Language server migrated to pygls 2.x** — the LSP server now targets
  pygls 2.x APIs (`pygls.lsp.server.LanguageServer`, lsprotocol
  2025 types, `PrepareRenamePlaceholder`, protocol-level
  `textDocument/publishDiagnostics` notifications) while staying
  backwards-compatible with pygls 1.3 for existing installs; the dependency
  constraint is now `pygls>=1.3.0,<3.0.0`.

## [0.4.5] - 2026-08-24

### Added
- **Service dependencies with `depends_on`** — declare start-up ordering
  directly in a service block, either bracketed (`depends_on: [db, redis]`)
  or bare (`depends_on: db`). The merged dependency view
  (`ServiceDef.dependencies`) combines the new field with the legacy
  `depends` list (de-duplicated, order-stable) so both forms compile
  identically, and `extends` inheritance carries the field over when the
  child does not override it. `infra fmt` round-trips the block and
  `infra graph` draws one edge per dependency.
- **Validator hard errors for broken dependency contracts** — a
  `depends_on` target that is not declared anywhere in the file (services
  *and* resources such as databases, caches and queues count; forward
  references are fine) fails validation with `DEPENDENCY_NOT_FOUND` and the
  hint `Declare service 'X' or fix spelling in depends_on`. Cycles
  (`A -> B -> A`, any length, self-loops included) are reported as
  `DEPENDENCY_CYCLE` with the offending path spelled out. The legacy
  `depends` list keeps its advisory `W001` warning for backward
  compatibility.
- **`depends_on` code generation in every backend** —
  Compose gains/keeps a `depends_on` mapping with
  `condition: service_healthy` per dependency; Kubernetes emits one
  `wait-for-<dep>` initContainer per edge (busybox `nc -z <dep> <port>`,
  ports resolved from the referenced definition: first service port, 5432
  for databases, 6379 for caches, 5672 for queues); Terraform materializes
  services as `kubernetes_deployment` resources with a matching
  `depends_on = [...]` reference list — plus the `kubernetes` provider —
  when (and only when) a program declares `depends_on`, mapping database
  targets to the provider's database resource and keeping unmappable
  targets as comments; Helm renders the same init-container waits driven by
  a new per-service `dependsOn` values key. Programs without `depends_on`
  produce byte-identical output to 0.4.4.
- **Batch workspace processing with `--all` / `-a`** — `infra check`,
  `infra validate`, `infra cost`, `infra doctor` and `infra fmt` gain a
  recursive workspace scan: every `*.infra` file under the current
  directory is processed (hidden directories and vendor folders such as
  `node_modules` are skipped), results render as a per-file status table
  with a one-line summary (`Checked 8 files: 8 valid, 0 errors`), and
  `--json` emits an aggregate document (`files`, `valid`, `errors`,
  `warnings`, per-file results; `cost` adds `total_monthly_usd`) for CI/CD
  pipelines. Exit codes: 1 when any file fails, 0 otherwise; invoking a
  command with neither files nor `--all` is a usage error (exit 2).

## [0.4.4] - 2026-08-23

### Fixed
- **Diamond imports no longer report false duplicate-variable errors** — when
  a module is reachable through two import paths (A imports B and C; both
  import D), the shared file is now merged exactly once. The resolver keys
  visited files by `(path, selection)` and deduplicates statement identity
  per merge level, so genuine duplicates declared in *different* files still
  raise `E001`.
- **Import recursion limit is actually enforced** — the depth counter was
  never incremented while recursing into imported files, so `max_depth` was
  dead code and a deeply nested chain crashed with a raw `RecursionError`.
  Over-deep chains now raise `ImportDepthError` (a compiler domain error)
  with the message `Import depth exceeded limit of 20` long before the
  interpreter limit. `DEFAULT_MAX_DEPTH = 20` remains the default and stays
  configurable via the resolver constructor.
- **Live drift probes degrade gracefully on a slow/hung Docker daemon** —
  the Compose path (`docker compose ps` + one `docker inspect` per
  container) used the full 30 s per-probe timeout with no global cap, so a
  hung daemon stalled `infra doctor --check-drift --live` / `infra diff
  --live` for N containers × 30 s. A global probe budget (`10 s`) now bounds
  the whole scan; `subprocess.TimeoutExpired` / `CalledProcessError` in
  individual inspect steps are caught and reported as a readable
  `DriftReport.error` (exit 1) while successfully gathered state is still
  compared — never false drift, never a zombie process.

### Performance
- **~10× faster compilation with imports** — the `Lark` LALR parser for the
  bundled grammar is now instantiated once per process and shared by the
  parser and the import resolver (previously recompiled for every imported
  file, ~0.7 s each; a file with 20 imports went from ~14.5 s to well under
  1.5 s). Custom grammar paths still build their own parser.

### Internal
- Coverage hardening round: `k8s_validator.py` and `analyzer/drift.py` are
  at 100% line & branch coverage, `analyzer/validator.py` at 96%, and
  `parser/transformer.py` at 99% — the proven-unreachable LALR fallback
  branches (`?rule` inlining artifacts, the unreferenced `duration` rule,
  the unproduced `FLOAT` terminal) carry `# pragma: no cover` instead of
  inflating the gap. New contract suites: `test_transformer_coercions.py`
  (50 tests), `test_semantic_validator_edges.py` (24), plus targeted error
  paths in `test_k8s_validator.py` and probe-budget tests in
  `test_cli_drift.py`. Total: 2438 tests passing, project coverage 96.7%
  line / 91.8% branch.

## [0.4.3] - 2026-08-22

### Added
- **Live plan & preview** — `infra diff app.infra --live` turns the diff
  command into the `terraform plan` equivalent: the desired spec from a
  single `.infra` file is compared against the **live** state of a Kubernetes
  namespace (`kubectl get`) or a Docker Compose stack (`docker compose ps` +
  `docker inspect` — strictly read-only, never mutating), and the planned
  changes are printed as a colored `rich` preview:
  `~ service "app":` followed by e.g. `replicas: 2 -> 5` and
  `image: "myapi:v1.0" -> "myapi:v1.1"`. Services absent in the live state
  are shown as `+` creations, in-sync ones as `=`, and a `Plan:` summary line
  closes the report with an `infra up` hint. Exits 0 when the live state
  already matches the spec, 1 when changes are pending (making it usable as
  a CI gate), 2 on usage errors. `-t/--target k8s|compose` selects the
  platform, `-n/--namespace` the k8s namespace, `-e/--env` applies an
  environment overlay before planning, and `--format json` emits a structural
  `DriftReport` payload for automation. The classic two-file diff mode is
  unchanged; the second file argument becomes optional and is rejected
  exactly when `--live` is used.
- **FinOps CI/CD guardrail** — `infra validate <file> --max-cost <USD>` and
  `infra check <file> --max-cost <USD>` compute the static monthly cost
  estimate (`CostAnalyzer`) and fail the pipeline with a `COST_EXCEEDED`
  validation error when the estimate breaches the budget, e.g.
  `Estimated monthly cost $330.00 exceeds the --max-cost budget of $200.00`,
  including the remediation hint
  `Hint: Reduce CPU/RAM requests or database instances to fit budget`. The
  comparison is strict (an estimate exactly equal to the budget passes), the
  flag composes with `-e/--env` overlays
  (`infra validate app.infra -e prod --max-cost 500` prices the overlay), and
  the error flows through all output formats (text, `--json`, `--format
  json|github`) for machine consumption. New public helpers:
  `analyzer.cost.budget_exceeded_message()` plus the `COST_EXCEEDED_CODE` /
  `COST_EXCEEDED_HINT` constants, and `SemanticValidator.validate(max_cost=...)`.

## [0.4.2] - 2026-08-22

### Added
- **Deep live drift detection** — `infra doctor --check-drift <file> --live`
  compares the declared `.infra` spec against the **running** infrastructure:
  Kubernetes (`kubectl get deployment,service -n <ns> -o json`) or Docker
  Compose (`docker compose ps --format json` + `docker inspect`). Compares
  replicas, container image, ports and literal environment variables, prints a
  `rich` In-Sync/Drifted summary table plus
  `[DRIFT] app: replicas expected 3, live 1 (MODIFIED)` lines, and exits 1 on
  drift. `--namespace/-n` selects the k8s namespace; `--json` emits a
  structural `DriftReport` for CI/CD gates. All probes are strictly
  **read-only** — the check never mutates the cluster or the daemon.
- **FinOps PR reports** — `infra cost --format/-f table|json|markdown|html`
  renders the cost estimate as a GitHub/GitLab-ready Markdown table
  (`CostEstimate.to_markdown()`) or an HTML table with escaped names
  (`CostEstimate.to_html()`), for pasting into pull-request comments and CI
  job summaries. `--output/-o <file>` writes the report to a file. The
  existing `--json` flag is preserved as an alias of `--format json`.

## [0.4.1] - 2026-08-22

### Added
- **Multi-environment overlays** — define `environment "name" { service ... }`
  blocks that override base service parameters (replicas, image, env, labels,
  annotations, resources, command, args) per deploy-time environment, without
  duplicating the base definitions.
- **`-e` / `--env` / `--environment` flag** on `compile`, `validate`, `up`,
  `down` and `cost` to select an environment overlay. Applying an unknown
  environment produces a clear error listing the available ones.
- **`apply_environment_overlay()`** analyzer API to merge an overlay onto a
  parsed program (env/labels/annotations are merged; overlay wins on name
  collisions).

## [0.4.0] - 2026-08-20

### Added
- **`infra up`** — direct execution: compiles a `.infra` file to a target and
  applies it. Supports `-t kubernetes` (`kubectl apply -f`), `-t compose`
  (`docker compose up -d`) and `-t helm` (`helm upgrade --install`). A
  `--dry-run` flag prints the commands without executing them; missing tools
  produce a clear error pointing at `infra doctor`.
- **`infra down`** — removes resources applied from a `.infra` file via the
  matching tool (`kubectl delete`, `docker compose down -v`, `helm uninstall`).
- **`infra cost`** — static monthly cloud cost estimation. Walks the AST and
  estimates per-resource cost using documented per-unit rates (vCPU, RAM,
  storage, managed DB/cache). Prints a `rich` table or structured JSON
  (`{"total_monthly_usd": float, "breakdown": [...]}`) for CI/CD cost gates.
  Supports `--currency USD|EUR|PLN` and `--json`.

## [0.3.2] - 2026-08-20

### Fixed
- **Helm UTF-8 BOM** — the generated `Chart.yaml`, `values.yaml`, `templates/*`
  and `values.schema.json` are now defensively stripped of any leading UTF-8
  BOM (`\ufeff`), both in the Helm backend and in `infra compile` before
  writing to disk. Helm's Go YAML parser rejects a file that begins with a BOM
  (`yaml: invalid leading UTF-8 octet`), which broke on Windows CI. All
  generated files are written with explicit `encoding="utf-8"` and are verified
  byte-clean (no `\xef\xbb\xbf`) by a regression test.
- **Docker daemon probe** — `have_docker()` keeps its short `docker info` probe
  (returns `False` when the daemon is unreachable). `test_compose_up_and_healthy`
  now also skips (rather than fails) when `docker compose up` hits a daemon that
  went away between the probe and the run — common on flaky Windows CI runners.

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
