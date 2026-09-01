# Infra Lang — 5-minute quickstart

## Install
```bash
pip install infra-lang
# with the language server (recommended for VS Code):
pip install 'infra-lang[lsp]'
# note: the server runs on pygls 1.3 or 2.x (since 0.5.0)
```

Verify: `infra --version`

> For the latest development version, install from Git:
> `pip install git+https://github.com/TuviDev/infra-lang.git`
> Requires Python 3.11+.

## Create your first .infra file
Save as `app.infra`:

```infra
service api {
    image: "nginx:1.25.3"
    port: 8080
    health: http("/health")
    resources {
        requests { cpu: 100m, memory: 128Mi }
        limits   { cpu: 500m,  memory: 256Mi }
    }
}

database db {
    type: postgres
    version: "15"
    storage: 10Gi
    ssl: true
    backup { enabled: true, schedule: "0 2 * * *" }
}

secret db-creds {
    url: from env "DATABASE_URL"
}
```

### Declare service dependencies (since 0.4.5)

Services can declare start-up ordering with `depends_on` (bracketed or
bare). Targets may be other services **or** resources such as databases,
caches and queues:

```infra
service api {
    image: "myapp:1.0"
    port: 8080
    depends_on: [db, cache]
}

service worker {
    image: "worker:2.0"
    depends_on: db          # bare form for a single dependency
}
```

An undeclared target is a hard error (`DEPENDENCY_NOT_FOUND`), and
dependency cycles (`A -> B -> A`) fail validation with `DEPENDENCY_CYCLE`.
The legacy `depends: [...]` list keeps working alongside it. Each backend
renders the ordering natively — Compose `depends_on`, Kubernetes/Helm
`initContainers` that wait on `<dep>:<port>`, and Terraform
`depends_on = [...]` references on generated `kubernetes_deployment`
resources. `infra graph app.infra` draws one edge per dependency.

### Store secrets in an external secret manager (since 0.5.0)

Declare the store once, bind any number of secrets to it with `store:`:

```infra
secret_store "vault_store" {
    provider: "vault"
    address: "https://vault.internal:8200"
    path: "secret/data/app"
}

secret api_keys {
    store: "vault_store"
    token: from env "API_TOKEN"
}
```

Supported providers are `vault`, `aws`, `gcp` and `kubernetes`. A dangling
`store:` reference fails validation with `STORE_NOT_FOUND`. The Kubernetes
backend compiles stores to `SecretStore` manifests and bound secrets to
`ExternalSecret` CRDs (External Secrets Operator); Compose marks them
`external: true`; Terraform generates the matching cloud secret-manager
resources. Legacy inline secrets keep working unchanged.

### Declare custom Kubernetes resources (since 0.5.0)

Any CRD can be declared inline with the generic `resource` block:

```infra
resource "custom_crd" "my_resource" {
    api_version: "stable.example.com/v1"
    kind: "MyKind"
    spec {
        replicas: 3
        template { labels { app: "web" } }
    }
}
```

Property values accept both `key: expression` and the bare-map form
`key { ... }` (nestable), and keys tolerate every DSL keyword. Missing
`api_version`/`kind` produce advisory warnings (`W010`/`W011`), duplicate
properties are hard errors (`E050`). The Kubernetes backend renders the
manifest verbatim, the Helm backend ships it under the chart's `crds/`
directory, and the Compose/Terraform backends emit an explicit skip
notice in their compilation warnings.

### Control network traffic between services (since 0.5.1)

Declare service-level network security policies natively:

```infra
network_policy "app_sec" {
    target: "api"
    allow_ingress: ["frontend"]
    allow_egress: ["db"]
    block_all_ingress: true
}
```

Any workload named by `target`, `allow_ingress` or `allow_egress` must
exist in the file — dangling references fail validation with
`POLICY_TARGET_NOT_FOUND`. Setting `block_all_ingress` together with
`allow_ingress` rules emits advisory `W012` (the allow rules win).

Each backend renders the policy natively: Kubernetes produces a
`NetworkPolicy` manifest (`podSelector` on the target, peer selectors,
`ingress: []` deny-all when only the block is set), Docker Compose puts
the target and its allowed peers on a dedicated bridge network
(`np_app_sec`) so unrelated containers cannot reach it, and Terraform
inlines an `aws_security_group`, pair of `google_compute_firewall`
rules, or an `azurerm_network_security_group` with priority-ordered
security rules — depending on the chosen provider.

### Open the visual dashboard (since 0.5.2)

Spin up a local interactive dashboard for any `.infra` file:

```bash
infra serve app.infra              # http://localhost:8080
infra serve app.infra --port 9000 --no-browser
infra serve app.infra -e prod      # apply an environment overlay
infra ui   app.infra               # alias of `infra serve`
```

The page shows the **architecture DAG** (services, databases, caches,
queues and `depends_on` edges; networks, secret stores and network
policies in a shared lane), the **FinOps calculator** (monthly cost per
resource with a share chart), a **drift panel**, and an **environment
preview switcher**. The HTML is regenerated on every request, so edits
to the file appear on reload. The server binds to `127.0.0.1` only and
uses nothing but the Python standard library.

Export the same view as a fully offline single-file report (no server
started):

```bash
infra serve app.infra --output-html report.html
```

### Compare environments side by side and export the DAG (since 0.5.5)

One command renders both environment overlays next to each other, with a
diff table (``+`` added, ``−`` removed, ``Δ`` changed) and per-panel
cost estimates. The special name `base` is the unoverlaid file:

```bash
infra serve app.infra --compare base prod          # live on localhost
infra serve app.infra --compare base prod -o cmp.html   # static report
infra ui    app.infra --compare base prod          # alias works too
```

`--compare` cannot be combined with `-e/--environment`; unknown overlay
names exit with code 1 and list the available environments. Identical
environments render a readable "No differences" empty state.

The architecture DAG used by the dashboard can be exported as a
self-contained `.svg` document (same collector and layout; opens in any
browser, no external assets):

```bash
infra graph app.infra -o dag.svg              # format inferred from .svg
infra graph app.infra --format svg            # to stdout
infra graph app.infra --format svg -e prod    # with overlay applied
```

The dashboard's Architecture tab also has a **Download SVG** button that
embeds exactly this document. PNG/PDF rasterization is intentionally not
bundled (it would require native rendering dependencies).

### Watch live drift in the dashboard (since 0.5.6)

The Drift tab can compare the file against the **live** state, using the
same read-only probe engine as `infra doctor --check-drift --live`
(`kubectl get` / `docker compose ps` + `docker inspect` — never a
mutation):

```bash
infra serve app.infra --live-drift                # k8s probe (kubectl)
infra serve app.infra --live-drift -t compose     # Docker Compose probe
infra serve app.infra --live-drift -n my-ns       # k8s namespace
infra serve app.infra --live-drift -o report.html # static export
```

The panel shows a green **IN-SYNC** badge, an amber **DRIFTED** badge with
a per-field diff table, or a red failure badge (``CLI TOOL MISSING``,
``PROBE TIMEOUT``, ``CLUSTER UNREACHABLE``) — a missing `kubectl`/`docker`
or an unreachable cluster degrades gracefully and never crashes the
server. `--live-drift` cannot be combined with `--compare` (the compare
report has no drift panel).

### Run the compiler in a browser: Web Playground + Web API (since 0.6.0)

The `web/` directory contains a complete **static playground** (Monaco
editor, Compose/Kubernetes/Terraform/SVG/dashboard tabs, shareable URLs,
waitlist section) that runs the real Python compiler in WebAssembly via
Pyodide. Host it on GitHub Pages or Vercel together with the
`infra_lang-0.7.1-py3-none-any.whl` — the page installs the wheel in the
browser at load time (no backend required):

```bash
python -m build                        # produces dist/infra_lang-0.7.1-py3-none-any.whl
cp dist/infra_lang-0.7.1-py3-none-any.whl web/
python -m http.server -d web 8000      # then open http://localhost:8000
```

The same in-memory surface is usable from any embedded Python through
**`infra.web_api`**:

```python
from infra import web_api
web_api.compile_to_target(source, target="compose", env_name=None)
# -> {"success": True, "files": {"docker-compose.yml": "...", ...}, "errors": []}
web_api.generate_ui_report(source)   # single-file dashboard/compare HTML
web_api.export_dag_svg(source)       # standalone architecture SVG
web_api.get_ast_json(source)         # JSON-safe AST dict
web_api.list_examples()              # embedded hello_world/web_app/microservices
```

`web_api` is guaranteed free of disk/process/browser side effects
(checked by dedicated tests), which makes it safe for WASM sandboxes and
serverless embeddings.

## Visualization & schema tooling (since 0.7.1)

```bash
# Native PNG architecture graph (Pillow drawing engine, dark theme)
infra graph app.infra --format png -o graf.png

# JSON Schema (draft-07) of the whole DSL for editors and tooling
infra schema -o infra-schema.json
```

The `infra serve` / `infra ui` dashboard embeds the interactive
architecture SVG and adds **Download SVG** / **Download PNG** buttons
(data-URI payloads — the browser saves the files with no server round
trip).

## Team integration: PR comments, alerts, policies (since 0.7.0)

```bash
# PR comment with change list + monthly cost delta + SEC*/REL* findings
infra ci-comment app.infra --base /tmp/base-from-main.infra \
    --max-monthly-cost 500 --fail-on-security > comment.md

# Slack / Teams / Discord alerts (also from .infra-alert.yml)
infra alert app.infra --webhook "$SLACK_WEBHOOK" --format slack \
    --max-monthly-cost 500 --live-drift --dry-run

# Declarative team policy (budgets, no secrets in env, no :latest tags)
infra policy-check app.infra                # reads ./infra-policy.yaml

# Static team dashboard site for GitHub Pages/S3 (offline, with history)
infra ui app.infra --publish site/
```

See [CI integration](ci_integration.md) for the composite GitHub Action
(`infra-check`) that posts/updates the comment on every pull request.

## Validate
```bash
infra validate app.infra
```

### Check the whole workspace at once (since 0.4.5)

`infra check`, `infra validate`, `infra cost`, `infra doctor` and
`infra fmt` all accept `--all` (`-a`): every `.infra` file under the
current directory is processed recursively (hidden folders and
`node_modules` are skipped), rendered as a status table with a one-line
summary:

```bash
infra validate --all
```

```
Validated 8 files: 8 valid, 0 errors
```

For CI/CD, `--json` emits an aggregate document with per-file results (and
`total_monthly_usd` for `infra cost --all`). The exit code is 1 when any
file fails, so a pipeline can gate on a single command.

### Budget guardrail for CI/CD

Fail the pipeline when the estimated monthly cost exceeds your budget
(FinOps gate). On breach, validation exits 1 with a `COST_EXCEEDED` error:

```bash
# exit 1 when the estimate exceeds $200/month
infra validate app.infra --max-cost 200

# combines with environment overlays — prices the "prod" variant
infra validate app.infra -e prod --max-cost 500

# the same guardrail exists on the syntax-only check command
infra check app.infra --max-cost 200
```

Output on breach:

```
error[COST_EXCEEDED] app.infra: Estimated monthly cost $330.00 exceeds the --max-cost budget of $200.00
  Hint: Reduce CPU/RAM requests or database instances to fit budget
```

## Compile to Kubernetes
```bash
infra compile app.infra --target kubernetes
```

## Compile to Docker Compose
```bash
infra compile app.infra --target compose
```

## See what it looks like
```bash
ls infra-out/
```

## Check the diff between two configs
```bash
cp app.infra app_v2.infra
# change replicas to 5 in api
infra diff app.infra app_v2.infra
```

## Preview changes against the live infrastructure

Like `terraform plan`: `infra diff --live` compares your `.infra` spec with
the **live** state of a Kubernetes namespace or a running Docker Compose
stack and shows the planned changes *before* you deploy. The probes are
strictly read-only — nothing on the cluster is ever modified.

```bash
# Plan against a Kubernetes namespace (default target)
infra diff app.infra --live --namespace default

# Plan against a running Docker Compose stack
infra diff app.infra --live --target compose

# Plan for a specific environment overlay
infra diff app.infra --live -e prod
```

Example output:

```
~ service "app":
    replicas: 2 -> 5
    image: "myapi:v1.0" -> "myapi:v1.1"

Plan: 0 to create, 1 to change (2 field change(s) across 1 service(s)); 2 unchanged.
Hint: run `infra up <file>` to apply the planned changes.
```

The command exits 0 when the live state already matches the spec and 1 when
changes are pending, so it doubles as a CI/CD gate. Add `--format json` for
a structural report instead of the colored preview.

> **Slow or hung Docker daemon?** Since 0.4.4 the Compose probes run under a
> global 10-second time budget: instead of stalling the terminal with one
> 30-second timeout per container, the command finishes promptly with a
> readable error (and reports whatever state it did manage to gather).

## Deploy it directly

Apply the compiled resources to a live platform (Kubernetes, Compose, or Helm)
with `infra up`, and remove them with `infra down`:

```bash
# Deploy to a Kubernetes cluster (requires kubectl on PATH)
infra up app.infra --target kubernetes

# Preview the commands without executing them
infra up app.infra --target kubernetes --dry-run

# Bring up a Docker Compose stack (requires the Docker daemon)
infra up app.infra --target compose

# Tear everything back down
infra down app.infra --target compose
```

Missing a tool? `infra up`/`infra down` tell you what's missing and point you
at `infra doctor`.

## Estimate the cost

Get a rough monthly cost estimate for your infrastructure:

```bash
infra cost app.infra                    # rich table
infra cost app.infra --json             # structured JSON for CI gates
infra cost app.infra --currency PLN     # other currencies
```

### FinOps reports for pull requests

Render the estimate as Markdown or HTML — ready to paste into a PR comment or
a CI job summary:

```bash
# Markdown table for a GitHub/GitLab PR comment
infra cost app.infra --format markdown

# HTML table (e.g. for a GitHub Actions job summary)
infra cost app.infra --format html

# Write the report straight to a file
infra cost app.infra --format markdown --output cost-report.md
```

In a GitHub Actions workflow you can post the report on every PR:

```yaml
- run: infra cost app.infra --format markdown --output cost.md
- uses: marocchino/sticky-pull-request-comment@v2
  with:
    path: cost.md
```

## Detect drift against the live infrastructure

After deploying, someone may `kubectl scale` a Deployment or hot-patch an
image — silently diverging from your `.infra` spec. Catch it with the live
drift check (read-only; it never mutates the cluster):

```bash
# Compare the spec against a live Kubernetes namespace
infra doctor --check-drift app.infra --live --target k8s --namespace default

# Compare against a running Docker Compose stack
infra doctor --check-drift app.infra --live --target compose

# Structured JSON for CI/CD gates (exit code 1 on drift)
infra doctor --check-drift app.infra --live --json
```

The check compares replicas, container image, ports and environment variables
and prints an In-Sync/Drifted table plus explicit drift lines:

```
[DRIFT] api: replicas expected 3, live 1 (MODIFIED)
```

Without `--live`, `infra doctor --check-drift` keeps its original behavior:
comparing the compiled output against on-disk generated files (`--out-dir`).

## What's next
- Read the tutorial: `docs/tutorial.md`
- See examples: `examples/`
