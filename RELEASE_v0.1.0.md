# Infra Lang v0.1.0

## What is it

Infra Lang is an Infrastructure-as-Code DSL with a LALR(1) grammar that
compiles a single, human-readable definition into Kubernetes, Docker Compose,
Terraform HCL, and GitHub Actions. It is built for developers who want one
source of truth for their infrastructure plus built-in quality checks.

## Installation

```bash
pip install infra-lang
```

Requires Python 3.11+.

## Why use it

- **One definition, many backends** — write `service api { ... }` once and
  compile it to Kubernetes, Docker Compose, Terraform or GitHub Actions.
- **Built-in linters** — 10 security rules (SEC001–SEC010) and 14 reliability
  rules (REL001–REL014) catch hardcoded secrets, mutable tags, missing backups
  and more before you deploy.
- **First-class scheduling** — `schedule` blocks emit CronJobs + HPA + RBAC,
  `autoscale` emits an HPA, `disruption` emits a PodDisruptionBudget.
- **Inline network topology** — `network_policy`, `topology` and
  `affinity` map directly to Kubernetes NetworkPolicy,
  TopologySpreadConstraints and pod affinity/anti-affinity.
- **Environment inheritance** — `environment prod extends dev` with resource
  quotas that become Kubernetes ResourceQuotas.

## Quick Start

```infra
# app.infra
service hello {
    image: "nginx:1.25.3"
    port: 80
    health http("/")
    resources { requests { cpu: 100m, memory: 64Mi } limits { cpu: 200m, memory: 128Mi } }
}
```

```bash
infra validate app.infra
infra compile app.infra --target kubernetes
```

## Features in v0.1.0

- Core DSL with LALR(1) grammar — 11 structures
  (service, database, cache, queue, storage, network, secret, config,
  pipeline, environment, cluster).
- 4 compilation backends: Kubernetes, Docker Compose, Terraform HCL,
  GitHub Actions.
- Security linter: SEC001–SEC010 (10 rules).
- Reliability linter: REL001–REL014 (14 rules).
- Kubernetes resources: Deployment, Service, Ingress, StatefulSet, PVC,
  Secret, ConfigMap, CronJob, HPA, PodDisruptionBudget, NetworkPolicy,
  ResourceQuota, Namespace, ServiceAccount, ClusterRole,
  ClusterRoleBinding, TopologySpreadConstraints.
- `schedule` blocks → CronJobs + HPA + RBAC; `autoscale` → HPA;
  `disruption` → PodDisruptionBudget.
- Inline `network_policy`, `topology`, `affinity`/anti-affinity.
- `environment.quotas` → ResourceQuota; `environment.extends` → inheritance.
- Import system with cycle detection; template string interpolation.
- CLI: `compile`, `validate`, `fmt`, `diff`, `graph`, `docs`, `repl`,
  `init`, `check`.
- `--var` injection, `--watch` live recompilation, `--validate-output`
  Kubernetes structure validation.
- Idempotent formatter; AST-based diff engine; interactive REPL.
- VS Code extension (syntax highlighting + snippets).

## Known limitations

- Linter rules are heuristics, not a substitute for a real Kubernetes
  admission controller / dry-run.
- `--validate-output` does structural checks only; it is not a full schema
  validator against the Kubernetes OpenAPI.
- No LSP server yet (planned).
- No automated end-to-end deployment tests against a live cluster.
- Terraform output is basic (cluster/resources); module generation is minimal.

## What's next (v0.2.0)

- LSP server.
- VS Code extension wired to LSP diagnostics.
- Drift detection between infra definitions and live resources.
- Package / registry support for reusable modules.
