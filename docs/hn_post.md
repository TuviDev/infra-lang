# HN Post Draft

## Title (max 80 chars)
Show HN: Infra Lang – IaC DSL that compiles to K8s, Compose, Terraform, GitHub Actions

## Body

Hi HN,

I built a DSL for infrastructure-as-code that compiles a single `.infra` file
to multiple targets simultaneously.

**The problem it solves:**
Defining the same microservice stack in Kubernetes requires 300+ lines of YAML
across 12 files. The same stack in Infra Lang is ~60 lines in one file.

**What's unique:**
- Single file → K8s YAML, Docker Compose, Terraform HCL, and GitHub Actions
- Built-in security linter (10 rules): detects hardcoded secrets, mutable
  image tags, privileged containers — at compile time, not runtime
- Built-in reliability linter (14 rules): thundering herd, even replicas in
  HA, Kafka without fault tolerance, database without backup, etc.
- `schedule {}` block compiles to CronJobs + HPA + RBAC
- `autoscale {}` block compiles to an HPA
- LSP server: errors and warnings shown inline in VS Code as you type

**Quick example:**

```infra
database main-db {
    type: postgres
    version: "15.4"
    storage: 20Gi
    ssl: true
    backup { enabled: true, schedule: "0 2 * * *" }
}

service api {
    image: "myapp/api:v1.0.0"
    replicas: 2
    port: 8080
    health http("/health")
    resources {
        requests { cpu: 200m, memory: 256Mi }
        limits   { cpu: 1000m, memory: 512Mi }
    }
    autoscale { min: 2, max: 10, target_cpu: 70 }
    depends: [main-db]
}
```

Compile it however you like:

```bash
infra compile app.infra --target kubernetes
infra compile app.infra --target compose
infra compile app.infra --target github
```

**Tech stack:**
- Python 3.11+, Lark LALR(1) parser
- pygls for the LSP server
- 1319 tests, 92% coverage

**Installation:**
```bash
pip install infra-lang
pip install 'infra-lang[lsp]'  # with LSP support
```

**GitHub:** [link]
**Docs:** [link]

I'm curious what use cases you'd like to see covered, and whether the
approach (DSL vs. YAML generators) makes sense to you.

## Notes for posting
- Post on a weekday morning (9-11am EST)
- Category: Show HN
- Expect questions about: why not Helm, why not Pulumi, performance,
  Terraform support depth

## Talking points (dla odpowiedzi na komentarze)

**Why not Helm?**
Helm is only K8s. Infra Lang targets 4 backends from one source, and has
built-in validation.

**Why not Pulumi?**
Pulumi uses real programming languages (Python/TypeScript). Infra Lang is a
purpose-built DSL — simpler, focused, with better linting for common infra
mistakes.

**Why not Terraform?**
Terraform is cloud infrastructure (VMs, networks). Infra Lang targets K8s
workloads primarily, with basic Terraform output as a bonus.

**What about Helm charts you already have?**
`infra compile` can be one step in your pipeline. It's not a full migration —
just an alternative for new services.
