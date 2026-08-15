# Infra Language Specification

**Version 0.1.0**

## 0. Stability

The following language features are **stable** and part of the v0.1.0 contract.
They will not change in a backwards-incompatible way within the v0.1.x line.
See [language_decisions.md](language_decisions.md) for the design decisions
and [versioning.md](versioning.md) for the deprecation policy.

| Feature | Status |
|---------|--------|
| service | stable |
| database | stable |
| cache | stable |
| queue | stable |
| storage | stable |
| network | stable |
| secret | stable |
| config | stable |
| pipeline | stable |
| environment | stable |
| cluster | stable |
| schedule | stable |
| autoscale | stable |
| disruption | stable |
| network_policy | stable |
| topology | stable |
| affinity | stable |
| environment.extends | stable |
| environment.quotas | stable |

## 1. Introduction

Infra is a domain-specific language for defining cloud infrastructure. An
`.infra` file describes services, databases, caches, queues, storage, networks,
secrets, configs, CI/CD pipelines, environments and clusters. It compiles to
Kubernetes YAML, Docker Compose, Terraform HCL (AWS/GCP/Azure) and GitHub
Actions.

```infra
service hello {
    image: "nginx:1.25.3"
    port: 80
}
```

## 2. Basic Syntax

- Files end with `.infra`.
- Comments: `# line` and `/* block */`.
- Whitespace is free-form (blocks are `{ }` delimited).
- Identifiers: letters, digits, `-` and `_` (must not begin with a digit).
- Keywords: `service database cache queue storage network secret config
  pipeline environment cluster let const import from as match if then else
  true false null in`.

## 3. Data Types

| Type     | Example        | Notes                        |
|----------|----------------|------------------------------|
| string   | `"hello"`      | double or single quotes      |
| int      | `42`, `0xFF`   | decimal, hex, binary         |
| float    | `3.14`         |                              |
| bool     | `true`/`false` |                              |
| null     | `null`         |                              |
| duration | `30s`, `5min`, `2h`, `7d`, `1w` | `min` = minutes |
| resource | `128Mi`, `500m`, `2Gi`, `2cores` | `m` = milli (CPU) |
| percentage | `25%`        |                              |
| list     | `[1, 2, 3]`    |                              |
| map      | `{a: 1}`       |                              |
| template | `` `hello {name}` `` | interpolates variables |

## 4. Expressions

Operators by precedence (lowest to highest): `||`, `&&`, `!`, comparisons
(`== != < <= > >=`), `+ -`, `* / %`, unary `-`, `**`, then atoms (literals,
identifiers, lists, maps, calls, template strings, `if`, `match`).

```infra
let scale = if env("ENV") == "prod" then 10 else 2
let m = match status { 200 -> "ok" _ -> "other" }
```

## 5. Variables

```infra
let port = 8080
const VERSION = "1.2.3"      # immutable
```

`const` values are immutable; `let` values may be reassigned.

## 6. Imports

```infra
import "./base.infra"
import "./lib.infra" as lib
from "./types.infra" import SMALL_CPU, MEDIUM_CPU
```

Imported files are loaded and merged at parse time; their top-level `const`/
`let` declarations become visible to the importing file.

## 7. Decorators

```infra
@prod
@replicas(3)
service api {
    image: "myapp:1.0"
}
```

## 8. Service

```infra
service api {
    image: "myapp:1.0"
    replicas: 3
    port 8080
    env { DB_URL: from secret "db".url }
    resources { requests { cpu: 100m, memory: 128Mi } }
    health http("/health")
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| image | string/template | — | container image |
| build | build block | — | docker build config |
| replicas | int | 1 | replica count |
| port | port spec | — | exposed ports |
| env | list | — | environment variables |
| envFrom | list | — | bulk env sources |
| resources | resources block | — | requests/limits |
| health | health spec | — | liveness/readiness probe |
| probes | probes block | — | liveness/readiness/startup |
| volumes | list | — | volumes |
| depends | list | — | dependencies |
| labels/annotations | map | — | metadata |
| strategy | block | rolling | deploy strategy |
| security | block | — | security context |
| lifecycle | block | — | preStop/postStart hooks |
| ingress | block | — | HTTP ingress |

Backends: Kubernetes → Deployment + Service (+Ingress); Compose → service.

## 9. Database

```infra
database db { type: postgres version: "15" storage: 20Gi backup { enabled: true } }
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| type | string | postgres | postgres/mysql/mongodb/... |
| version | string | — | engine version |
| replicas | int | 1 | replica count |
| ha | bool | false | high availability |
| storage | resource | — | storage size |
| backup | backup block | — | backup config |
| users | list | — | db users |

Backends: Kubernetes → StatefulSet + Service; Terraform → RDS/SQL.

## 10. Cache

```infra
cache session { type: redis maxmemory: 512Mi persistence: true }
```

## 11. Queue

```infra
queue events { type: rabbitmq topics { tasks: { partitions: 3 } } }
```

## 12. Storage

```infra
storage assets { type: s3 bucket: "my-assets" region: "eu-west-1" }
```

## 13. Network

```infra
network main { cidr: "10.0.0.0/16" subnets { a: { cidr: "10.0.1.0/24" } } }
```

## 14. Secret

```infra
secret db-creds { password: from env "DB_PASSWORD" }
```

## 15. Config

```infra
config app { log_level: "info" }
```

## 16. Pipeline

```infra
pipeline ci {
    trigger { branches: ["main"] }
    stages { test: { runsOn: "ubuntu-latest" steps { t: { run: "pytest" } } } }
}
```

## 17. Environment

```infra
environment dev { namespace: "myapp-dev" labels: { env: "dev" } }
```

## 18. Cluster

```infra
cluster main { provider: aws nodes { w: { machine type: "t3.medium" min: 1 max: 5 } } }
```

## 19. Standard Library

`env`, `secret`, `config`, `upper`, `lower`, `trim`, `replace`, `contains`,
`starts_with`, `ends_with`, `split`, `join`, `len`, `min`, `max`, `abs`,
`clamp`, `length`, `concat`, `first`, `last`, `range`, `coalesce`, `if_env`.

## 20. Backends

| Backend   | `infra compile -t` | Generates                       |
|-----------|--------------------|---------------------------------|
| Kubernetes| `kubernetes`       | Deployments, StatefulSets, ...  |
| Compose   | `compose`          | docker-compose.yml + .env       |
| Terraform | `terraform`        | main.tf, variables.tf, ...      |
| GitHub    | `github`           | .github/workflows/*.yml         |

## 21. Error Codes

| Code | Meaning |
|------|---------|
| E001 | Undefined variable / duplicate variable |
| E002 | Duplicate global name |
| E003 | Invalid replicas (must be >= 1) |
| E004 | Port out of range |
| E005 | Invalid type value |
| E010 | Service missing image/build / invalid schedule cron |
| E011 | Invalid replicas / schedule replicas < 1 |
| E012 | Port out of range |
| E013 | Duplicate port |
| E014 | Duplicate env variable |
| E020 | Unknown database type |
| E021 | Database replicas < 1 |
| E022 | Invalid backup cron schedule |
| E023 | Duplicate database user |
| E024 | Unknown cache type |
| E025 | Unknown queue type |
| E026 | Unknown storage type |
| E027 | Duplicate secret key |
| E030 | Stage depends on undefined stage |
| E031 | Cyclic pipeline dependency |
| E032 | Invalid cron schedule |
| E033 | Unknown cloud provider |
| E040 | Empty name |

### Security Linter (SEC)

| Code | Rule | Severity |
|------|------|----------|
| SEC001 | Hardcoded secret in environment variable | Error |
| SEC002 | Value matches a known credential pattern | Error |
| SEC003 | Mutable image tag (`latest`, `dev`, ...) | Warning |
| SEC004 | Privileged container | Error |
| SEC005 | Running as root (UID 0) | Warning |
| SEC006 | Database SSL explicitly disabled | Warning |
| SEC007 | Hardcoded value inside a secret block | Error |
| SEC008 | Service exposed via ingress without a network_policy | Warning |
| SEC009 | Image uses Docker Hub (no registry prefix) | Warning |
| SEC010 | Secret sourced from an env var in a prod environment | Warning |

### Reliability Linter (REL)

All REL rules are warnings (they do not block compilation).

| Code | Rule |
|------|------|
| REL001 | High replicas without startup probe |
| REL002 | Even HA replica count |
| REL003 | No memory limit |
| REL004 | No health checks |
| REL005 | Deep dependency chain |
| REL006 | Database without backup |
| REL007 | Single-replica depended-on service |
| REL008 | Redis without persistence |
| REL009 | No graceful shutdown (preStop) |
| REL011 | Autoscale without CPU limits |
| REL012 | Autoscale plus a fixed `replicas` (conflicting) |
| REL013 | Database without resource allocation |
| REL014 | Kafka with a single replica (no fault tolerance) |

## 22. Grammar

See `src/infra/lexer/grammar.lark` for the full Lark EBNF grammar.
