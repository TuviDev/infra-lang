# Reddit Post Draft

## Subreddits
- r/devops (primary)
- r/kubernetes
- r/programmingtools

## Title
Show r/devops: I built a DSL that compiles to K8s YAML, Docker Compose,
Terraform, and GitHub Actions from a single file — with built-in security linter

## Body

Cześć!

Zbudowałem narzędzie IaC, które rozwiązuje problem, który miałem: musiałem
utrzymywać ten sam serwis zdefiniowany w K8s YAML, Compose i GitHub Actions.
Każda zmiana w 3 miejscach.

**Infra Lang** pozwala napisać to raz:

```infra
service api {
    image: "myapp/api:v1.0.0"
    replicas: 2
    port: 8080
    health http("/health")
    resources {
        requests { cpu: 200m, memory: 256Mi }
        limits   { cpu: 1000m, memory: 512Mi }
    }
    depends: [main-db]
}
```

I skompilować do wybranego targetu:

```bash
infra compile app.infra --target kubernetes
infra compile app.infra --target compose
infra compile app.infra --target github
```

**Bonus features:**
- Security linter wykrywa hardcoded secrets zanim trafią do repo (SEC001)
- Reliability linter ostrzega przed Kafka bez replikacji, bazą bez backupu,
  serwisem bez health checks
- Live errors w VS Code przez LSP server

GitHub: [link]
pip install infra-lang

Czekam na feedback — szczególnie czy takie podejście (własny DSL) ma sens vs.
generowanie YAML przez Python.
