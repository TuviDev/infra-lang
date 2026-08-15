# Real Kubernetes Validation Report

## Environment
- docker: **no** (blocks `kind` cluster creation)
- kubectl: **no** (no cluster to connect to)
- kind: **yes** (v0.24.0 binary downloaded to /tmp/kind) — but requires docker to create a cluster
- kubeconform: **yes** (v0.8.0 binary downloaded to /tmp/kubeconform)

**Verdict note:** A live cluster (kind + kubectl apply) cannot be created in
this sandbox because there is no Docker runtime. The **real Kubernetes
JSON-schema validation** via `kubeconform -strict` against the official
Kubernetes schemas *is* fully executed and gives strong confidence that the
generated YAML is valid for the API server.

## Example 1: 01_hello_world.infra
- validate: **PASS** (exit 0, 1 warning)
- compile: **PASS**
- schema validation (kubeconform -strict): **PASS** — 2/2 resources valid
- kind apply: **BLOCKED** (no docker)
- kubectl get all: **BLOCKED** (no cluster)
- issues found: none
- fixes applied: none

## Example 2: 02_web_app.infra
- validate: **PASS** (exit 0, 2 warnings)
- compile: **PASS**
- schema validation (kubeconform -strict): **PASS** — 7/7 resources valid
- kind apply: **BLOCKED** (no docker)
- kubectl get all: **BLOCKED**
- issues found: none
- fixes applied: none

## Example 3: demo/main.infra
- validate: **PASS** (exit 0, 12 warnings — all reliability/security hints)
- compile: **PASS**
- schema validation (kubeconform -strict): **PASS** — 11/11 resources valid
- kind apply: **BLOCKED** (no docker)
- kubectl get all: **BLOCKED**
- issues found: none
- fixes applied: none

## Additional coverage (all public examples)
| Example | Resources | Valid | Invalid |
|---------|-----------|-------|---------|
| 01_hello_world | 2 | 2 | 0 |
| 02_web_app | 7 | 7 | 0 |
| 03_microservices | 11 | 11 | 0 |
| 04_cicd_pipeline | — | — | — (targets GitHub) |
| corpus/realistic/01_web_app | 7 | 7 | 0 |
| corpus/realistic/02_microservices | 12 | 12 | 0 |
| corpus/realistic/04_full_stack | 9 | 9 | 0 |

## Final verdict
**Real K8s test status: PARTIAL** — real JSON-schema validation (kubeconform
`-strict`, official Kubernetes schemas) **PASSES** for every generated
resource (0 invalid across all examples). Live `kind apply` + `kubectl` is
**BLOCKED** purely by the missing Docker runtime in this sandbox, not by any
code issue.

**What this proves:** the generated YAML is structurally valid per the
official Kubernetes OpenAPI/JSON schemas for every resource kind we emit
(Deployment, Service, Ingress, StatefulSet, Secret, ConfigMap, HPA, PDB,
NetworkPolicy, ResourceQuota). The only remaining step before absolute
deployment confidence is running `kubectl apply` against a live cluster,
which requires Docker — an environment limitation, not a product defect.
