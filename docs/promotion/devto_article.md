# Dev.to Article Draft â€” "One infrastructure definition, many targets"

> Target: 900â€“1100 words. Adjust as needed.

## The problem

Most platforms today describe the same application in several different
formats. The deployment runs on Kubernetes, so there are YAML manifests. Local
development uses Docker Compose, so there is a `docker-compose.yml`. CI runs on
GitHub Actions, so there is a workflow file. If you're on a cloud provider,
there may also be Terraform.

Each of these is maintained by hand, and each change is made in several places.
They drift. The worst part is *when* you find out: a typo in a Kubernetes
manifest usually fails at `kubectl apply`, not when you write it. A misnamed
port, an invalid base64 secret, a missing label â€” all of these are caught by
the API server, after you've already committed and maybe deployed.

What if you wrote the infrastructure once, and it was validated as you wrote it?

## The idea

Infra Lang is an Infrastructure-as-Code DSL. You describe your application in a
single `.infra` file, and the compiler turns it into Kubernetes YAML, Docker
Compose, Terraform HCL, or a GitHub Actions workflow.

Here is a service:

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
}
```

That one block produces a Kubernetes `Deployment` and a `Service`. The same
file produces a Docker Compose service and a GitHub Actions job. You maintain
one source of truth, and you get the outputs you need.

## Why a DSL and not a YAML template

Templating layers (Helm, string interpolation in CI) push this problem down the
road: you still write YAML, just with placeholders, and validation still
happens late.

A DSL gives you a few things for free:

- **A real parser.** The `.infra` syntax is parsed by a hand-written LALR(1)
  grammar with `{}` blocks. Syntax errors are caught immediately, with a
  location and a helpful message â€” not after you've deployed.
- **Compile-time linting.** The analyzer runs security and reliability checks
  before anything is emitted. Ten security rules catch hardcoded secrets,
  mutable image tags, and privileged containers. Thirteen reliability rules
  catch a database without a backup, a thundering-herd replica count, and a
  single-replica Kafka. `Error`-severity findings block the compile.
- **One mental model.** Service, database, cache, queue, secret, pipeline â€”
  the vocabulary matches what you actually build, not the individual YAML
  documents each platform needs.

## What it compiles to

Five targets today:

| Target | What you get |
|--------|--------------|
| Kubernetes | Deployments, Services, Ingress, StatefulSets, PVCs, ConfigMaps, Secrets, CronJobs, HPA, PDBs, NetworkPolicies, ResourceQuotas, Namespaces, RBAC, TopologySpreadConstraints |
| Helm | a complete chart: `Chart.yaml`, `values.yaml`, `templates/`, `_helpers.tpl` |
| Docker Compose | `docker-compose.yml`, `.env.example`, a `Makefile` |
| Terraform | `main.tf`, `variables.tf`, `outputs.tf`, `providers.tf` for AWS/GCP/Azure |
| GitHub Actions | a workflow plus a `dependabot.yml` |

Not every resource maps to every target. A `pipeline` compiles only to GitHub
Actions; a `cluster` compiles only to Terraform. The support matrix documents
the exact mapping.

## An example: a small microservices stack

Three services, a database, and a queue:

```infra
database users-db { type: postgres }

queue events { type: rabbitmq }

service auth-svc {
    image: "myapp/auth:v2.1.0"
    port 3001
    replicas: 2
    depends: [users-db]
    health http("/health")
}

service api-svc {
    image: "myapp/api:v2.1.0"
    replicas: 3
    depends: [auth-svc, events]
    health http("/health")
}
```

Compile it:

```bash
infra compile app.infra --target kubernetes
infra compile app.infra --target compose
```

`depends` becomes a `depends_on` in Compose and, in Kubernetes, is reflected in
ordering and network policy. The same stack is described once.

## How I test the output

Validation only helps if it reflects what the platform will actually accept.
Two things keep the output honest:

1. **Schema validation.** Generated Kubernetes is checked against the official
   schemas with `kubeconform -strict`. All public examples pass.
2. **Live E2E.** There's an opt-in test suite (`pytest -m live_e2e`) that
   compiles the examples, starts a `kind` cluster, runs `kubectl apply` on the
   generated YAML, and verifies the contracts â€” that secrets are valid base64,
   that multi-port services have named ports, that resources carry the right
   labels. This is what caught two real bugs before release.

## Editor support

There's a language server and a VS Code extension. You get live diagnostics,
hover documentation, context-aware completion, go-to-definition, find-
references, workspace symbols, and rename â€” across every `.infra` file in the
project on disk, not just the one you have open.

## Limitations

I want to be upfront about what's not done yet:

- **Terraform output is structural.** It emits resources, variables, and
  providers, but no modules, data sources, or remote state yet.
- **GitHub Actions output doesn't support reusable workflows**
  (`workflow_call`).
- **The LSP is single-process.** Cross-file rename over files not open in the
  editor isn't supported yet.

## Getting started

```bash
pip install 'infra-lang[lsp]'

infra validate app.infra
infra compile app.infra --target kubernetes
```

The [quickstart](https://github.com/TuviDev/infra-lang/blob/main/docs/quickstart.md)
takes about five minutes, and the repo has commented examples from a hello-world
service up to a multi-service stack with a CI/CD pipeline.

## Where this could go

The roadmap is editor polish, `kind`/`minikube` helpers (`infra up`,
`infra verify`), Terraform modules, GitHub reusable workflows, and â€” based on
feedback â€” a plugin system.

If you're maintaining the same app in multiple formats, I'd love your take on
the language and the target coverage.

---

*Infra Lang is MIT-licensed and on [GitHub](https://github.com/TuviDev/infra-lang).*

