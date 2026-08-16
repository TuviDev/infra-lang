# Infra Lang — Tutorial

Ready to go from `pip install` to working infrastructure in about 15 minutes.
Every `infra` block below is a fully working example.

## Prerequisites
- Python 3.11+

## Installation

```bash
pip install 'git+https://github.com/kakukpl/infra-lang.git'
infra --version
```

You should see a version number, e.g. `0.1.0`.

---

## Lesson 1: Your first service (3 min)

Save this file as `hello.infra`:

```infra
service hello {
    image: "nginx:1.25.3"
    port: 80
    health http("/")
    resources { requests { cpu: 100m, memory: 64Mi } limits { cpu: 200m, memory: 128Mi } }
}
```

Validate the syntax and semantics:

```bash
infra validate hello.infra
```

A valid file reports no errors. Now compile it to Kubernetes:

```bash
infra compile hello.infra --target kubernetes --dry-run
```

You'll see a generated `Deployment` and `Service`. The same file compiles to
Docker Compose with no changes to the source:

```bash
infra compile hello.infra --target compose --dry-run
```

**What happened:** Infra parsed your file, built an AST, validated it, and
rendered the manifests for the selected backend.

---

## Lesson 2: Databases and secrets (4 min)

You don't want to hardcode passwords in source files. Use `secret` + `from env`:

```infra
secret db-creds {
    url: from env "DATABASE_URL"
}

database main-db {
    type:    postgres
    version: "15.4"
    storage: 20Gi
    ssl:     true
    backup { enabled: true schedule: "0 2 * * *" retention: 30d }
}

service api {
    image: "myapp/api:1.0.0"
    replicas: 2
    port: 8080
    health http("/health")
    resources { requests { cpu: 200m, memory: 256Mi } limits { cpu: 1000m, memory: 512Mi } }
    env { DATABASE_URL: from secret "db-creds".url }
    depends: [main-db]
}
```

### How Infra catches hardcoded secrets?

If you put a password directly in `env`:

```infra
service api {
    image: "nginx:latest"
    env { PASSWORD: "supersecret" }
}
```

`infra validate` reports:

```
error[SEC001] ... Hardcoded secret detected: 'PASSWORD' in service 'api'
              appears to contain a sensitive value.
Found 1 errors and 4 warnings
```

**Fix:** replace the literal with a source from a secret manager:

```infra
service api {
    image: "myapp/api:1.0.0"
    env { PASSWORD: from secret "db-creds".password }
}
```

This way the secret never ends up in your repository.

---

## Lesson 3: Reliability hints (3 min)

The built-in reliability linter points out ways to make your infrastructure
more robust.

### REL003 — no memory limit

```infra
service api {
    image: "myapp/api:1.0.0"
    replicas: 5
    resources { requests { cpu: 200m } }
}
```

Warning: the service has `requests` but no memory `limits` → OOM risk.

**Fix:** add `limits { memory: 256Mi }` to the `resources` block.

### REL006 — database without a backup

```infra
database main-db {
    type: postgres
    storage: 20Gi
}
```

Warning: the database has no backup enabled.

**Fix:** add a `backup { enabled: true schedule: "0 2 * * *" }` block.

---

## Lesson 4: Multiple environments (3 min)

Define environments and inherit from one another:

```infra
environment dev {
    namespace: "myapp-dev"
    labels: { env: "dev" }
}

environment prod extends dev {
    namespace: "myapp-prod"
    quotas { max_cpu: 10cores max_memory: 20Gi max_pods: 100 }
}
```

`prod` inherits from `dev`, overrides the namespace, and adds `ResourceQuota`
limits.

---

## Lesson 5: A CI/CD pipeline (2 min)

Define a pipeline and Infra generates a GitHub Actions workflow:

```infra
pipeline build {
    trigger { branches: ["main"] }
    stages {
        test: { runsOn: "ubuntu-latest" steps { run: "pytest -q" } }
        build: { needs: [test] runsOn: "ubuntu-latest" steps { run: "docker build -t app ." } }
        deploy: { needs: [build] runsOn: "ubuntu-latest" steps { run: "kubectl apply -f deploy/" } }
    }
}
```

```bash
infra compile build.infra --target github
```

---

## What's next

- [Language spec](language_spec.md) — full grammar and structures.
- [Examples](https://github.com/kakukpl/infra-lang/tree/main/examples) —
  ready-made projects (`01_hello_world.infra`, `03_microservices.infra`,
  `04_cicd_pipeline.infra`).
- [README](https://github.com/kakukpl/infra-lang) — overview of features and
  backends.
