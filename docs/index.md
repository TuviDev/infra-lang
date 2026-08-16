---
hide:
  - navigation
  - toc
---

# Write infrastructure once, compile it to Kubernetes, Compose, or GitHub Actions

**Infra Lang** is an Infrastructure-as-Code DSL for DevOps engineers, SREs, and
platform teams. Describe your application — services, databases, queues,
secrets, and pipelines — in one declarative `.infra` file, and compile it to the
target you need.

<div class="grid cards" markdown>

- :material-rocket-launch: **Get started**

    Go from `pip install` to working infrastructure in 5 minutes.

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

- :material-github: **View on GitHub**

    The source code, issues, and discussions live on GitHub.

    [:octicons-arrow-right-24: GitHub repo](https://github.com/kakukpl/infra-lang)

</div>

---

## The problem

Infrastructure is maintained in many formats at once: raw Kubernetes YAML, a
local `docker-compose.yml`, a GitHub Actions workflow, maybe Terraform. Each is
written and maintained by hand, so they drift apart — and mistakes in the
Kubernetes manifests only surface at `kubectl apply` time, not when you write
them.

Infra Lang replaces the fragmentation with a single, validated source of truth.

## One source, many targets

Write the service once:

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

Which produces a Deployment and a Service:

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

The same file compiles to Docker Compose and (via a `pipeline` block) to a
GitHub Actions workflow.

## Features

<div class="grid cards" markdown>

- :material-code-json: **11 resource types**

    `service`, `database`, `cache`, `queue`, `storage`, `network`, `secret`,
    `config`, `pipeline`, `environment`, `cluster`.

- :material-server-network: **4 compilation targets**

    Kubernetes (17 resource kinds), Docker Compose, Terraform HCL (AWS/GCP/Azure),
    GitHub Actions.

- :material-shield-check: **Security & reliability linters**

    10 security rules (SEC001–SEC010) and 13 reliability rules (REL001–REL014),
    with actionable hints. Error-severity findings block compilation.

- :material-language-server: **A real language server**

    Live diagnostics, hover docs, completion, go-to-definition, find-references,
    workspace symbols, and rename across every `.infra` file on disk.

- :material-flask: **Live E2E validation**

    An opt-in suite that really applies the generated Kubernetes to a `kind`
    cluster and really runs `docker compose up` on a Docker daemon.

- :material-lock-open-variant: **Free & open source**

    MIT-licensed. Compiler-grade validation and formatting built in.

</div>

## Getting started in three steps

1. **Install**

    ```bash
    pip install 'git+https://github.com/kakukpl/infra-lang.git'
    ```

2. **Write a `.infra` file** (see the demo above).

3. **Compile to your target**

    ```bash
    infra validate app.infra
    infra compile app.infra --target kubernetes
    ```

## Real examples

| Example | What it shows |
|---------|---------------|
| [01_hello_world](https://github.com/kakukpl/infra-lang/blob/main/examples/01_hello_world.infra) | The simplest single service |
| [02_web_app](https://github.com/kakukpl/infra-lang/blob/main/examples/02_web_app.infra) | API + database + cache + secrets |
| [03_microservices](https://github.com/kakukpl/infra-lang/blob/main/examples/03_microservices.infra) | Three services sharing a DB and a queue |
| [04_cicd_pipeline](https://github.com/kakukpl/infra-lang/blob/main/examples/04_cicd_pipeline.infra) | A full CI/CD pipeline (GitHub Actions) |

## Community

- [GitHub repository](https://github.com/kakukpl/infra-lang)
- [Report an issue](https://github.com/kakukpl/infra-lang/issues)
- [Discussions](https://github.com/kakukpl/infra-lang/discussions)

<small>Infra Lang is inspired by the ideas behind Terraform, Score, and Pulumi.</small>
