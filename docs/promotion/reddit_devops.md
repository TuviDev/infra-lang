# Reddit Draft — r/devops and r/kubernetes

## Title

"Opensource: write infra once, compile to Kubernetes, Compose, or GitHub Actions"

## Body

I built an open-source DSL called Infra Lang that compiles a single `.infra`
file to Kubernetes YAML, Docker Compose, Terraform HCL, or a GitHub Actions
workflow.

**Why I built it.** The teams I've worked with describe the same service in at
least three places: the deployment manifests, a local docker-compose, and a CI
pipeline. Every change touches all of them, they drift, and the K8s mistakes
only show up when `kubectl apply` rejects them.

**What it does.**

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

That's a Deployment + Service in Kubernetes, and the equivalent service in
docker-compose and a GitHub Actions job. One file, multiple targets.

**The part I think is genuinely useful:**

- Validation and linting happen at **compile time**, not apply time. 10 security
  rules (hardcoded secrets, mutable tags, privileged containers) and 13
  reliability rules (thundering herd, no DB backup, single-replica Kafka).
  `Error`-severity findings block the compile.
- An **LSP server** for VS Code: diagnostics, completion, go-to-definition,
  rename — across the whole project on disk.
- I actually **test it against a real cluster**: an opt-in live-E2E suite spins
  up `kind`, runs `kubectl apply` on the generated YAML, and verifies the
  contracts (base64 secrets, named multi-port services, labels).

**Honest caveats:**

- Terraform output is structural — no modules or remote state yet.
- GitHub Actions output doesn't support reusable workflows yet.
- It's a young project (v0.1.0, MIT). PyPI release next week.

If you maintain infrastructure in multiple formats and are tired of the drift,
give it a look. Feedback welcome.

Repo: https://github.com/kakukpl/infra-lang
