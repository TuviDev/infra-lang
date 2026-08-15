# Infra Examples

| File | What it shows |
|------|---------------|
| `01_hello_world.infra` | The simplest single-service definition. |
| `02_web_app.infra` | A typical stack: API + PostgreSQL + Redis + secrets. |
| `03_microservices.infra` | Three coordinated services sharing a DB and a message queue. |
| `04_cicd_pipeline.infra` | A full CI/CD pipeline that compiles to GitHub Actions. |

## Usage

```bash
infra validate examples/01_hello_world.infra
infra compile examples/01_hello_world.infra --target kubernetes
infra compile examples/02_web_app.infra --target kubernetes --split
infra compile examples/03_microservices.infra --target compose
infra compile examples/04_cicd_pipeline.infra --target github
```
