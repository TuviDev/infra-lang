# Infra Lang

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-v0.1.0-orange)

**Infra Lang** is an Infrastructure-as-Code DSL that compiles a single
declarative source file to Kubernetes YAML, Docker Compose, Terraform HCL
(AWS/GCP/Azure) and GitHub Actions. It brings compiler-grade validation,
built-in security and reliability linting, and a formatter/REPL to IaC.

## The Problem

Infrastructure today is written in many different formats: raw YAML manifests,
Helm charts, Terraform files, Docker Compose files, CI pipelines. Each has its
own syntax, validation gap, and sharp edges.

Compare a raw Kubernetes Deployment + Service + Ingress (60+ lines of YAML,
easy to get subtly wrong) with the equivalent Infra:

```infra
service api {
    image: "myapp/api:v1.0.0"
    replicas: 3
    port 8080
    health http("/health")
    resources {
        requests { cpu: 200m, memory: 256Mi }
        limits   { cpu: 1000m, memory: 512Mi }
    }
    ingress { host: "api.example.com", tls: true }
}
```

One concise, type-checked source that compiles to the correct Kubernetes
objects — with validation, security checks and formatting built in.

## Features

- [x] Own LALR(1) grammar — readable, indentation-free, `{}` blocks
- [x] 11 top-level structures: `service`, `database`, `cache`, `queue`,
      `storage`, `network`, `secret`, `config`, `pipeline`, `environment`,
      `cluster`
- [x] 4 backends: Kubernetes, Docker Compose, Terraform, GitHub Actions
- [x] Semantic validation with 30+ error codes and helpful hints
- [x] Built-in **Security linter** (SEC001–SEC010)
- [x] Built-in **Reliability linter** (REL001–REL014)
- [x] Template-string interpolation (`` `image:{TAG}` ``)
- [x] Import system with cycle detection
- [x] `extends` inheritance for environments and services
- [x] Time-aware scaling (`schedule` → CronJobs + HPA + RBAC)
- [x] Autoscaling block (`autoscale` → HPA)
- [x] Disruption budgets (`disruption` → PDB)
- [x] Infra Diff engine (`infra diff`)
- [x] Built-in formatter (`infra fmt`) and REPL (`infra repl`)
- [x] 25+ stdlib functions and a prelude of reusable constants
- [x] 1100+ tests, clean `ruff` and `mypy`

## Installation

```bash
pip install infra-lang
```

With LSP support (recommended for VS Code):

```bash
pip install 'infra-lang[lsp]'
```

Verify:

```bash
infra --version
infra --help
```

### VS Code / LSP

Install the [VS Code extension](vscode-infra-lang/README.md) for syntax
highlighting, snippets, and a language server that provides live diagnostics,
hover docs, context-aware completion, document outline, **whole-project**
workspace indexing (go-to-definition, find-references and workspace symbols
across every `.infra` file on disk, not just open tabs), symbol rename, and
formatting. See [docs/lsp.md](docs/lsp.md).

### Opt-in error reporting

Anonymous error reporting is **off by default**. Check the status with:

```bash
infra feedback
```

Enable it locally with `infra feedback --on`. It never sends source code,
file paths, or PII. See [docs/lsp.md](docs/lsp.md) for details.

For development:

```bash
git clone https://github.com/infra-lang/infra-lang
cd infra-lang
pip install -e ".[dev]"
```

## Quick Start

**Step 1 — write a service:**

```infra
# app.infra
service api {
    image: "nginx:1.25.3"
    port: 80
    health http("/")
}
```

**Step 2 — validate it:**

```bash
infra validate app.infra
# ✅ No errors found
```

**Step 3 — compile to Kubernetes:**

```bash
infra compile app.infra --target kubernetes
# ✅ Compiled 2 files to ./infra-out/
```

**Step 4 — inspect the output:**

```bash
infra compile app.infra --target kubernetes --dry-run
```

**Step 5 — iterate with fmt and diff:**

```bash
infra fmt app.infra
infra diff app.infra app.new.infra
```

## Backends

| Backend     | Command           | What it generates |
|-------------|-------------------|-------------------|
| Kubernetes  | `-t kubernetes`   | Deployments, Services, Ingress, StatefulSets, HPA, PDB, CronJobs, NetworkPolicies, ConfigMaps, Secrets |
| Docker Compose | `-t compose`  | `docker-compose.yml`, `.env.example`, `Makefile` |
| Terraform   | `-t terraform`    | `main.tf`, `variables.tf`, `outputs.tf`, `providers.tf` (AWS/GCP/Azure) |
| GitHub Actions | `-t github`    | `.github/workflows/*.yml`, `dependabot.yml` |

## Built-in Quality Gates

Validation runs before every compile and flags problems with codes, source
locations, and actionable hints.

### Security Linter

| Code | Rule |
|------|------|
| SEC001 | Hardcoded secret in environment variable |
| SEC002 | Value matches a known credential pattern (OpenAI/GitHub/AWS/JWT) |
| SEC003 | Mutable image tag (`latest`, `dev`, ...) |
| SEC004 | Privileged container |
| SEC005 | Container running as root (UID 0) |
| SEC006 | Database SSL explicitly disabled |
| SEC007 | Hardcoded value inside a `secret` block |
| SEC008 | Service exposed via ingress without a `network_policy` |
| SEC009 | Image uses Docker Hub (no registry prefix) |
| SEC010 | Secret sourced from an env var in a prod environment |

### Reliability Linter

| Code | Rule |
|------|------|
| REL001 | High replicas without a startup probe (thundering herd) |
| REL002 | Even HA replica count (split-vote risk) |
| REL003 | No memory limit (OOM risk) |
| REL004 | No health checks |
| REL005 | Deep dependency chain (cascade-failure risk) |
| REL006 | Database without a backup |
| REL007 | Single-replica service that others depend on |
| REL008 | Redis cache without persistence |
| REL009 | No graceful-shutdown (`preStop`) hook |
| REL011 | Autoscale without CPU limits (HPA can't compute utilization) |
| REL012 | Autoscale plus a fixed `replicas` (conflicting) |
| REL013 | Database without resource allocation |
| REL014 | Kafka with a single replica (no fault tolerance) |

## CLI Reference

| Command    | Options | Description |
|------------|---------|-------------|
| `infra compile` | `-t`, `-o`, `--split`, `--var`, `--dry-run`, `--watch` | Compile to a backend |
| `infra validate` | `--strict`, `--format`, `--var` | Validate without compiling |
| `infra fmt` | `--check`, `--diff`, `--indent` | Format .infra files |
| `infra repl` | `--target`, `--history` | Interactive REPL |
| `infra init` | `--template`, `--target` | Scaffold a project |
| `infra check` | — | Quick syntax check |
| `infra graph` | — | Print dependency graph |
| `infra docs` | `-o` | Generate a Markdown inventory |
| `infra diff` | `--format`, `--only-changes` | Compare two .infra files |
| `infra lsp` | `--tcp`, `--host`, `--port` | Start the language server |
| `infra feedback` | `--on`, `--off`, `--project` | Manage opt-in error reporting |

## Language Reference

Each structure uses `{}` blocks; fields are `name: value`.

**Service:**

```infra
service api {
    image: "myapp:1.0"
    replicas: 3
    port 8080
    env { MODE: "prod" }
    resources { requests { cpu: 100m, memory: 128Mi } }
    health http("/health")
}
```

**Database:**

```infra
database db {
    type: postgres
    version: "15"
    storage: 20Gi
    backup { enabled: true, schedule: "0 2 * * *" }
}
```

**Pipeline:**

```infra
pipeline ci {
    trigger { branches: ["main"] }
    stages {
        test: { runsOn: "ubuntu-latest" steps { t: { run: "pytest" } } }
    }
}
```

**Variables, template strings and imports:**

```infra
const VERSION = "1.2.3"
import "./base.infra"

service api {
    image: `myapp:{VERSION}`
}
```

## Documentation

| Doc | What it covers |
|-----|----------------|
| [Language spec](docs/language_spec.md) | Full DSL reference (blocks, fields, errors) |
| [Tutorial](docs/tutorial.md) | 5-lesson guided intro |
| [Quickstart](docs/quickstart.md) | 5-minute start |
| [Design decisions](docs/language_decisions.md) | Syntax conventions & rationale |
| [Support matrix](docs/support_matrix.md) | Backend / K8s version support |
| [Known limitations](docs/known_limitations.md) | Honest boundaries of the project |
| [Versioning policy](docs/versioning.md) | Semantic versioning & deprecation |
| [Feedback policy](docs/feedback_policy.md) | Opt-in telemetry: what's sent, what's not |
| [Troubleshooting](docs/troubleshooting.md) | Common issues & how to report bugs |
| [LSP](docs/lsp.md) | Language server capabilities |
| [Roadmap v0.2.0](docs/roadmap_v0.2.0.md) | Upcoming plans |

## Examples

| File | Shows |
|------|-------|
| `examples/01_hello_world.infra` | The simplest single service |
| `examples/02_web_app.infra` | API + database + cache + secrets |
| `examples/03_microservices.infra` | Three services sharing a DB and a queue |
| `examples/04_cicd_pipeline.infra` | A full CI/CD pipeline (**GitHub target only**; no K8s output — expected) |
| `examples/demo/` | A complete commented microservices project (multi-file, validate + compile) |

```bash
infra compile examples/01_hello_world.infra --target kubernetes
```

## Development

```bash
git clone https://github.com/infra-lang/infra-lang
cd infra-lang
pip install -e ".[dev]"

# run tests (parallel)
pytest -n auto

# quality gates
ruff check src/
mypy src/infra --ignore-missing-imports

# build a wheel
python -m build
```

## License

MIT
