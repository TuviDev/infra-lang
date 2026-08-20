# Infra Lang

**Write infrastructure once, compile it to Kubernetes, Compose, or GitHub Actions.**

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
- **Reusable pieces** — template-string interpolation, `import` with cycle
  detection, `extends` inheritance, 25+ stdlib functions and a prelude of
  shared constants.

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
