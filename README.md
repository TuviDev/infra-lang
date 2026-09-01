# Infra Lang

**Write infrastructure once, compile it to Kubernetes, Compose, or GitHub Actions.**

[![PyPI](https://img.shields.io/pypi/v/infra-lang)](https://pypi.org/project/infra-lang/)
[![CI](https://img.shields.io/github/actions/workflow/status/TuviDev/infra-lang/ci.yml?branch=main)](https://github.com/TuviDev/infra-lang/actions)
[![Docs](https://img.shields.io/badge/docs-online-blue)](https://TuviDev.github.io/infra-lang/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

Infra Lang is an Infrastructure-as-Code DSL for DevOps engineers, SREs, and
platform teams. You describe your application — services, databases, queues,
secrets, and pipelines — in one declarative `.infra` file, and Infra Lang
compiles it to Kubernetes YAML, Docker Compose, Terraform HCL, or a GitHub
Actions workflow. Instead of hand-writing and maintaining the same app in four
different formats, you maintain one source of truth.

## Quick demo

A single `.infra` file describes a service:

```infra
# app.infra
service api {
    image: "myapp/api:v1.0.0"
    replicas: 3
    port 8080
    health http("/health")
    resources {
        requests { cpu: 200m, memory: 256Mi }
        limits   { cpu: 1000m, memory: 512Mi }
    }
}
```

Compile it to Kubernetes:

```bash
infra compile app.infra --target kubernetes
```

Infra Lang produces the matching Deployment and Service:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  replicas: 3
  selector:
    matchLabels:
      app.kubernetes.io/name: api
  template:
    spec:
      containers:
        - name: api
          image: myapp/api:v1.0.0
          ports:
            - containerPort: 8080
              name: port-0
          resources:
            requests: { cpu: 200m, memory: 256Mi }
            limits:   { cpu: 1000m, memory: 512Mi }
          readinessProbe:
            httpGet: { path: /health, port: 8080 }
---
apiVersion: v1
kind: Service
metadata:
  name: api
spec:
  selector:
    app.kubernetes.io/name: api
  ports:
    - port: 8080
      targetPort: 8080
```

The same file compiles to Docker Compose with no rewriting:

```bash
infra compile app.infra --target compose
```

A `pipeline` block compiles to a GitHub Actions workflow:

```infra
pipeline ci {
    trigger { branches: ["main"] }
    stages {
        test: { runsOn: "ubuntu-latest" steps { t: { run: "pytest" } } }
    }
}
```

```bash
infra compile app.infra --target github
```

## Features

- **11 top-level resource types** — `service`, `database`, `cache`, `queue`,
  `storage`, `network`, `secret`, `config`, `pipeline`, `environment`,
  `cluster`.
- **5 compilation targets** — Kubernetes (17 resource kinds), **Helm charts**,
  Docker Compose, Terraform HCL (AWS/GCP/Azure), GitHub Actions.
- **Compiler-grade validation** — 30+ error codes with source locations and
  actionable hints; invalid configs fail before anything is emitted.
- **Built-in security linter** (SEC001–SEC010) and **reliability linter**
  (REL001–REL014); `Error`-severity findings block compilation.
- **A language server** — context-aware completion, hover docs, live
  diagnostics with links and related info, go-to-definition, find-references,
  workspace symbols, symbol rename, signature help, document highlight,
  semantic tokens, folding, formatting, and quick-fixes — all across every
  `.infra` file on disk.
- **A formatter, REPL, and diff engine** — `infra fmt`, `infra repl`, and
  `infra diff` for reviewing changes.
- **Direct execution** — `infra up` / `infra down` apply and remove resources
  on a live cluster (`kubectl apply/delete`), Docker Compose
  (`docker compose up/down`), or Helm (`helm upgrade --install`/`uninstall`),
  with a `--dry-run` to preview commands.
- **Cost estimation** — `infra cost` estimates the monthly cloud cost of a
  `.infra` file (per-resource table, `--json` for CI gates, `--currency`).
- **Visual infrastructure dashboard** — `infra serve` / `infra ui` render any
  `.infra` file as an interactive, fully-offline HTML dashboard (see below).
- **Reusable pieces** — template-string interpolation, `import` with cycle
  detection, `extends` inheritance, 25+ stdlib functions and a prelude of
  shared constants.

## Visual Infrastructure Dashboard (`infra serve` / `infra ui`)

Since **0.5.2** Infra Lang ships a local, zero-dependency dashboard that
renders any `.infra` file as a single self-contained HTML page:

```bash
infra serve app.infra                    # http://localhost:8080 (opens browser)
infra serve app.infra --port 9000        # custom port (loopback only)
infra serve app.infra --no-browser       # serve without opening a browser
infra serve app.infra -e staging         # preview an environment overlay
infra serve app.infra -o report.html     # one-shot static export, then exit
infra ui app.infra                       # alias for `infra serve`
```

The dashboard shows the **architecture DAG** (services, databases, caches and
queues with `depends_on` edges), a **FinOps cost report** (monthly estimate
with a per-resource share chart), a **live-drift panel** and a switcher for
every `environment` overlay declared in the file. It inlines all CSS/JS — no
CDN, no external requests — and binds to `127.0.0.1` only.

Since **0.5.5** the same commands also compare two environments side by side
(diff table with added/removed/changed rows and per-side cost estimates) and
export the architecture DAG as a self-contained SVG:

```bash
infra serve app.infra --compare base prod          # served compare page
infra serve app.infra --compare base prod -o cmp.html   # static report
infra graph app.infra -o dag.svg                   # or: --format svg
```

`base` refers to the file without any overlay. The Architecture tab of the
dashboard additionally embeds a **Download SVG** button with the very same
document.

Since **0.5.6** the Drift tab can also probe the **live** state (read-only
`kubectl` / `docker compose` probes — the same engine as
`infra doctor --check-drift --live`):

```bash
infra serve app.infra --live-drift            # k8s probe (kubectl)
infra serve app.infra --live-drift -t compose # Docker Compose probe
```

The panel renders **IN-SYNC** / **DRIFTED** badges with a per-field diff
table, or a readable failure badge (``CLI TOOL MISSING``, ``PROBE
TIMEOUT``, ``CLUSTER UNREACHABLE``) when the tool or cluster is
unreachable — it never crashes the server.

## Web Playground & In-Memory Web API (since 0.6.0)

The full compiler can run **in your browser** via WebAssembly (Pyodide) —
no installation, no server round-trip. The `web/` directory ships a
static playground (host it on GitHub Pages/Vercel along with the
`infra_lang-*-py3-none-any.whl`): Monaco editor with `.infra` syntax
highlighting, example picker, one-click outputs for Docker Compose /
Kubernetes / Terraform, the architecture SVG and the visual dashboard, a
`?code=<base64>` share link, and an enterprise waitlist section.

The browser talks to **`infra.web_api`** — a pure in-memory API that also
works in any embedded Python (notebooks, serverless, CI bots):

```python
from infra import web_api

result = web_api.compile_to_target(source, "compose")  # success/files/errors
html   = web_api.generate_ui_report(source)            # dashboard (or compare)
svg    = web_api.export_dag_svg(source)                # architecture graph
ast    = web_api.get_ast_json(source)                  # JSON-safe AST
web_api.list_examples()                                # hello_world, web_app, …
```

`web_api` never touches the disk, processes or a browser API — errors are
returned as data — so it is safe to embed anywhere.

## Visualization & Schema export (since 0.7.1)

**Native PNG architecture graphs** — the dashboard's architecture DAG
exports to PNG with a pure-Python Pillow drawing engine (dark theme,
rounded node cards with image tags, arrowed edges — no Cairo, Graphviz
or headless browser):

```bash
infra graph app.infra --format png -o graf.png   # or just: -o graf.png
```

The dashboard served by `infra serve` / `infra ui` also offers one-click
**Download SVG** and **Download PNG** buttons on the architecture card —
payloads travel as data URIs, so saving is instant and offline.

**JSON Schema of the DSL** — editor/tooling integration via draft-07:

```bash
infra schema -o infra-schema.json   # or stdout without -o
```

The schema covers every top-level block (`service`, `database`,
`environment`, `network_policy`, `secret_store`, …) with exact type
enums and documented properties.

## Team Integration: CI comments, alerts, policies (since 0.7.0)

Everything a team needs around pull requests — at zero cost, no SaaS:

**PR comments with cost delta & security** — `infra ci-comment` renders a
Markdown report (changes, monthly cost delta vs `--base`, SEC*/REL*
findings) ready for `gh pr comment`, with CI gates:

```bash
infra ci-comment infra/app.infra --base /tmp/base.infra \
    --max-monthly-cost 500 --fail-on-security   # exit 1 when a gate fails
```

Or use the ready-made GitHub Action (see
[`docs/ci_integration.md`](docs/ci_integration.md)):

```yaml
- uses: TuviDev/infra-lang/.github/actions/infra-check@v0.7.1
  with:
    files: "infra/**/*.infra"
    base-ref: origin/main
    max-monthly-cost: "500"
    fail-on-security: "true"
```

**Alerts** — Slack / Teams / Discord webhooks for budget overruns,
security violations and live drift, from flags or `.infra-alert.yml`
(never log full webhook URLs — they carry secrets):

```bash
infra alert infra/app.infra --webhook "$SLACK_WEBHOOK" --format slack \
    --max-monthly-cost 500 --live-drift -t k8s -n default
```

**Team policies** — declarative `infra-policy.yaml` (budgets, no
hardcoded secrets in env, no `:latest` tags), enforced with stable
`POLxxx` codes:

```bash
infra policy-check infra/app.infra          # auto-discovers ./infra-policy.yaml
infra policy-check infra/app.infra -p policy.yaml -f json
```

**Static team dashboard** — publish the visual dashboard as an offline
site for GitHub Pages/S3, with JSON summary and append-only cost/drift
history:

```bash
infra ui infra/app.infra --publish site/    # index.html + envs/ + data/
```

## Try it in Codespaces

Click the button below to open this project in GitHub Codespaces:

[![Open in Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/TuviDev/infra-lang)

No local installation needed — full dev environment in about 2 minutes
(Python 3.12, Docker-in-Docker, kubectl/helm, Ruff/Mypy extensions).

## Installation

```bash
pip install infra-lang
```

With the language server (recommended for VS Code):

```bash
pip install 'infra-lang[lsp]'
```

Verify:

```bash
infra --version
infra --help
```

> **Note:** For the latest development version, install from Git:
> `pip install git+https://github.com/TuviDev/infra-lang.git`

**Requirements:** Python 3.11+.

## Getting started

Full documentation is hosted at **[TuviDev.github.io/infra-lang](https://TuviDev.github.io/infra-lang/)**.

The fastest path is the [5-minute quickstart](https://TuviDev.github.io/infra-lang/quickstart/). In short:

1. **Write** a `.infra` file (see the demo above).
2. **Validate** it: `infra validate app.infra`
3. **Compile** to a target: `infra compile app.infra --target kubernetes`
4. **Inspect** the output in `infra-out/`, or preview with `--dry-run`.
5. **Iterate** with `infra fmt app.infra` and `infra diff app.infra app2.infra`.

There is also a [guided tutorial](https://TuviDev.github.io/infra-lang/tutorial/) and
commented [examples](examples/).

## Supported targets

| Target | Command | What it generates |
|--------|---------|-------------------|
| **Kubernetes** | `-t kubernetes` | Deployments, Services, Ingress, StatefulSets, PVCs, ConfigMaps, Secrets, CronJobs, HPA, PDBs, NetworkPolicies, ResourceQuotas, Namespaces, RBAC, TopologySpreadConstraints |
| **Helm** | `-t helm` | A complete chart: `Chart.yaml`, `values.yaml`, `templates/`, `_helpers.tpl`, `.helmignore` |
| **Docker Compose** | `-t compose` | `docker-compose.yml`, `.env.example`, `Makefile` |
| **Terraform** | `-t terraform` | `main.tf`, `variables.tf`, `outputs.tf`, `providers.tf` (AWS/GCP/Azure) |
| **GitHub Actions** | `-t github` | `.github/workflows/*.yml`, `dependabot.yml` |

Not every resource type maps to every target — for example, `pipeline` compiles
only to GitHub Actions, and `cluster` only to Terraform. See the
[support matrix](https://TuviDev.github.io/infra-lang/support_matrix/) for the
full mapping.

## Documentation

The documentation is hosted at **[TuviDev.github.io/infra-lang](https://TuviDev.github.io/infra-lang/)**.

| Doc | What it covers |
|-----|----------------|
| [Quickstart](https://TuviDev.github.io/infra-lang/quickstart/) | 5-minute first run |
| [Language spec](https://TuviDev.github.io/infra-lang/language_spec/) | Full DSL reference (blocks, fields, error codes) |
| [Support matrix](https://TuviDev.github.io/infra-lang/support_matrix/) | Which resources map to which targets |
| [LSP / editor support](https://TuviDev.github.io/infra-lang/lsp/) | VS Code extension and language server |
| [Known limitations](https://TuviDev.github.io/infra-lang/known_limitations/) | Honest boundaries of the project |

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to
set up a dev environment, add a backend or a grammar rule, and the coding
standards (ruff, mypy). Please read our [Security policy](SECURITY.md) before
reporting a vulnerability.

## License

Licensed under the [MIT License](LICENSE).

---

Infra Lang is inspired by the ideas behind [Terraform](https://www.terraform.io/),
[Score](https://score.dev/), and [Pulumi](https://www.pulumi.com/): declarative
infrastructure that is easy to read and hard to get wrong.
