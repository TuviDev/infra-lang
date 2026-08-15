# Demo Script — Infra Lang v0.1.0

A narrated ~3.5 minute walkthrough. The example files live in
`examples/demo_script/`.

## Setup (30s)

```bash
pip install infra-lang
mkdir demo && cd demo
```

## Scene 1: The Problem (30s)

Show a typical Kubernetes deployment YAML: 40+ lines, repeated labels, no
validation. Then contrast: "what if this were 10 lines with type checking?"

## Scene 2: The Solution (60s)

Create `demo.infra`:

```infra
service api {
    image: "myapp:v1.0.0"
    port 8080
    ingress { host: "api.example.com" tls: true }
    health http("/health")
    resources {
        requests { cpu: 200m, memory: 256Mi }
        limits   { cpu: 1000m, memory: 512Mi }
    }
    autoscale { min: 2, max: 10, target_cpu: 70 }
}

database db {
    type: postgres
    version: "15"
    storage: 20Gi
    ssl: true
    backup { enabled: true, schedule: "0 2 * * *" }
}

secret db-creds {
    url: from env "DATABASE_URL"
}
```

## Scene 3: Security linter (30s)

```bash
infra validate demo.infra
```

Show: no errors, a few reliability hints.

Now intentionally add a hardcoded secret:

```infra
service api {
    image: "myapp:v1.0.0"
    env { PASSWORD: "bad123" }
}
```

```bash
infra validate demo.infra
```

Show: `SEC001` error with a hint pointing at `from secret`.

## Scene 4: Compile (30s)

```bash
infra compile demo.infra --target kubernetes
wc -l infra-out/infra.yaml
```

Show: compact input (a few lines) vs. the expanded multi-resource YAML output
(Deployment, Service, Ingress, HPA, Secret).

## Scene 5: Multi-backend (30s)

```bash
infra compile demo.infra --target compose
infra compile demo.infra --target github
ls infra-out/
```

Show that the same source produces Compose and GitHub Actions too.

## Scene 6: Diff (15s)

```bash
# change replicas to 5 in a copy
infra diff demo.infra demo_v2.infra
```

Show the `SUMMARY` line and before/after values.

## Scene 7: Graph (15s)

```bash
infra graph demo.infra
```

Show the ASCII dependency graph.

## Total time: ~3.5 minutes
