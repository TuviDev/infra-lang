# Dev.to Article Outline

## Title
"I replaced 300 lines of Kubernetes YAML with a 60-line DSL — here's how"

## Tags
kubernetes, devops, python, opensource

## Intro (hook)
Opisz problem: utrzymywanie K8s YAML dla mikroserwisu — ile plików, ile
powtórzeń, jak łatwo coś zepsuć.

## Section 1: The Problem
Pokaż realny przykład K8s YAML dla prostego serwisu. Policz linie. Wskaż
powtórzenia (labels, selector, image) i brak walidacji (literówki w YAML nie
są wykrywane aż do `kubectl apply`).

## Section 2: The Solution
Pokaż ten sam serwis w Infra Lang. Policz linie. Pokaż `infra compile`.

## Section 3: Built-in Safety
Pokaż SEC001 (hardcoded secret). Pokaż REL006 (database without backup).
Wyjaśnij, że to działa przy każdym compile — nie dopiero przy deployment.

## Section 4: Multi-backend
Pokaż, że ten sam plik → K8s + Compose + GitHub Actions. Pokaż przykładowy
output każdego.

## Section 5: Advanced features
`schedule {}`, `autoscale {}`, `network_policy {}` — z przykładami, co
generują (CronJobs, HPA, NetworkPolicy).

## Section 6: Getting started
```bash
pip install infra-lang
```
Prosty przykład end-to-end (service + validate + compile).

## Conclusion
Link do GitHub, zaproszenie do feedbacku.

## Estimated length: 1500-2000 words
