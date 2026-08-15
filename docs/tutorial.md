# Infra Lang — Tutorial

Gotowy na to, by przejść od `pip install` do działającej infrastruktury w
około 15 minut. Wszystkie bloki `infra` poniżej są w pełni działającymi
przykładami.

## Wymagania
- Python 3.11+

## Instalacja

```bash
pip install infra-lang
infra --version
```

Powinieneś zobaczyć numer wersji, np. `0.1.0`.

---

## Lekcja 1: Pierwszy serwis (3 min)

Zapisz ten plik jako `hello.infra`:

```infra
service hello {
    image: "nginx:1.25.3"
    port: 80
    health http("/")
    resources { requests { cpu: 100m, memory: 64Mi } limits { cpu: 200m, memory: 128Mi } }
}
```

Waliduj składnię i semantykę:

```bash
infra validate hello.infra
```

Dobrego pliku nie zawiera błędów. Teraz skompiluj go do Kubernetes:

```bash
infra compile hello.infra --target kubernetes --dry-run
```

Zobaczysz wygenerowany `Deployment` i `Service`. Ten sam plik możesz
skompilować do Docker Compose bez żadnych zmian w źródle:

```bash
infra compile hello.infra --target compose --dry-run
```

**Co się stało:** Infra sparsował Twój plik, zbudował AST, zwalidował go i
wyrenderował manifesty dla wybranego backendu.

---

## Lekcja 2: Baza i sekrety (4 min)

Nie chcemy hardcodować haseł w plikach źródłowych. Użyj `secret` + `from env`:

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

### Jak Infra łapie hardcoded sekrety?

Gdybyś wpisał hasło wprost w `env`:

```infra
service api {
    image: "nginx:latest"
    env { PASSWORD: "supersecret" }
}
```

`infra validate` zgłosi:

```
error[SEC001] ... Hardcoded secret detected: 'PASSWORD' in service 'api'
              appears to contain a sensitive value.
Found 1 errors and 4 warnings
```

**Naprawa:** zamień literał na źródło z secret managera:

```infra
service api {
    image: "myapp/api:1.0.0"
    env { PASSWORD: from secret "db-creds".password }
}
```

W ten sposób sekret nigdy nie trafia do repozytorium.

---

## Lekcja 3: Reliability hints (3 min)

Wbudowany linter reliability podpowiada, jak poprawić niezawodność
infrastruktury.

### REL003 — brak limitu pamięci

```infra
service api {
    image: "myapp/api:1.0.0"
    replicas: 5
    resources { requests { cpu: 200m } }
}
```

Ostrzeżenie: serwis ma `requests` ale nie ma `limits` pamięci → ryzyko OOM.

**Naprawa:** dodaj `limits { memory: 256Mi }` do bloku `resources`.

### REL006 — baza bez backupu

```infra
database main-db {
    type: postgres
    storage: 20Gi
}
```

Ostrzeżenie: baza nie ma włączonego backupu.

**Naprawa:** dodaj blok `backup { enabled: true schedule: "0 2 * * *" }`.

---

## Lekcja 4: Multi-environment (3 min)

Definiuj środowiska i dziedzicz po nich:

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

`prod` dziedziczy po `dev`, nadpisuje namespace i dodaje limity `ResourceQuota`.

---

## Lekcja 5: CI/CD Pipeline (2 min)

Zdefiniuj pipeline, a Infra wygeneruje GitHub Actions:

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

## Co dalej

- [Specyfikacja języka](language_spec.md) — pełna gramatyka i struktury.
- [Przykłady](../examples/) — gotowe projekty (`01_hello_world.infra`,
  `03_microservices.infra`, `04_cicd_pipeline.infra`).
- [README](../README.md) — przegląd funkcji i backendów.
