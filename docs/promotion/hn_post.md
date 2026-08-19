# HN Post Draft (Show HN)

## Title (max 80 chars)

"Show HN: Infra Lang â€“ write infra once, compile to K8s, Compose, or CI"

(74 chars.)

## Body

Hi HN,

I've been maintaining the same microservice stack in a few different formats â€”
Kubernetes YAML, docker-compose for local dev, a GitHub Actions workflow for
CI â€” and every change had to be made in each of them by hand. They drifted
apart, and mistakes in the K8s YAML only surfaced at `kubectl apply` time.

So I wrote a DSL that keeps one source of truth and compiles it to the target
you need:

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

That's a Kubernetes Deployment + Service. The same file also compiles to
docker-compose and to a GitHub Actions workflow.

What's worth a look:

- **A real parser, not a YAML front-end.** A hand-written LALR(1) grammar with
  `{}` blocks. Syntax errors are caught before anything is emitted.
- **Compile-time linting.** 10 security rules (hardcoded secrets, mutable
  image tags, privileged containers) and 13 reliability rules (thundering
  herd, DB without a backup, Kafka with a single replica). Error-severity
  findings block compilation.
- **An LSP server** for VS Code â€” live diagnostics, completion, go-to-definition
  and rename across the whole project on disk, not just the open tab.
- **A language server protocol client** packaged as a VS Code extension.
- **The K8s output is validated.** I run a live-E2E suite that actually applies
  the generated YAML to a `kind` cluster with `kubectl` and checks the
  contracts (base64 secrets, named multi-port services, managed-by labels).

Install:

```bash
pip install 'infra-lang[lsp]'
```

Available on PyPI: pip install infra-lang

Honest limitations: Terraform output is structural (no modules yet), GitHub
Actions doesn't support reusable workflows, and the LSP is single-process.

Would love feedback on the language design and the target coverage. Thanks!

## Comments to preempt

- *"Why not just use YAML / Helm?"* â€” the point is the single source of truth
  and validation, not the syntax. You can still emit the YAML you need.
- *"Why a new language?"* â€” because I wanted type-checked, lintable
  infrastructure, not another templating layer.
- *"Terraform is incomplete"* â€” yes; it's structural today, modules are on the
  roadmap.

