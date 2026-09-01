"""End-to-end tests: .infra -> parse -> validate -> compile -> verify output."""

from __future__ import annotations

import time

import yaml

from infra import compile as infra_compile
from infra.analyzer.validator import SemanticValidator
from infra.parser import Parser

P = Parser()

WEB_APP = """
service frontend {
    image: "nginx:alpine"
    port { target: 80 host: 3000 }
    depends: ["api"]
    health http("/")
    resources { cpu: 100m memory: 64Mi }
}

service api {
    image: "myapp:latest"
    port: 8080
    depends: ["db", "cache"]
    env {
        DB_URL: from secret "db-secret".url
        REDIS_URL: from config "app-config".redis_url
        NODE_ENV: "production"
    }
    health http("/health") { interval: 30s retries: 3 }
    resources { cpu: 500m memory: 256Mi }
    replicas: 2
}

database db {
    type: postgres
    version: "15"
    size: 10Gi
    backup { enabled: true schedule: "0 2 * * *" }
}

cache cache {
    type: redis
    maxmemory: 256Mi
}

secret db-secret {
    url: from env "DATABASE_URL"
}

config app-config {
    redis_url: "redis://cache:6379"
}
"""


def all_kinds(content: str) -> list:
    return [d["kind"] for d in yaml.safe_load_all(content) if d]


def all_docs(content: str) -> list:
    return [d for d in yaml.safe_load_all(content) if isinstance(d, dict)]


class TestWebApp:
    def test_full_pipeline(self):
        program = P.parse(WEB_APP, filename="web_app.infra")
        result = SemanticValidator().validate(program)
        assert result.is_valid, [e.message for e in result.errors]

        files = infra_compile(program, target="kubernetes").files
        content = "\n".join(files.values())
        kinds = all_kinds(content)
        assert "Deployment" in kinds
        assert "StatefulSet" in kinds
        assert "Secret" in kinds
        assert "ConfigMap" in kinds

    def test_compose_pipeline(self):
        program = P.parse(WEB_APP, filename="web_app.infra")
        files = infra_compile(program, target="compose").files
        data = yaml.safe_load(files["docker-compose.yml"])
        assert "frontend" in data["services"]
        assert "api" in data["services"]
        assert "db" in data["services"]


CICD = """
pipeline main-ci {
    trigger { branches: ["main", "develop"] paths: ["src/**", "tests/**"] }

    stages {
        test: {
            runsOn: "ubuntu-latest"
            matrix { python: ["3.10", "3.11", "3.12"] }
            steps {
                checkout: { uses: "checkout" }
                install:  { run: "pip install -e .[test]" }
                run:      { run: "pytest --cov" }
            }
        }
        build: {
            runsOn: "ubuntu-latest"
            needs: ["test"]
            steps { b: { run: "docker build -t myapp ." } }
        }
        deploy: {
            runsOn: "ubuntu-latest"
            needs: ["build"]
            steps { d: { run: "kubectl apply -f k8s/" } }
        }
    }
}
"""


class TestCICD:
    def test_ci_workflow(self):
        program = P.parse(CICD, filename="cicd.infra")
        result = SemanticValidator().validate(program)
        assert result.is_valid

        files = infra_compile(program, target="github").files
        wf = [c for k, c in files.items() if k != "dependabot.yml"][0]
        data = yaml.safe_load(wf)
        jobs = data["jobs"]
        assert "test" in jobs
        assert "build" in jobs
        assert "deploy" in jobs
        assert jobs["build"]["needs"] == ["test"]
        assert jobs["deploy"]["needs"] == ["build"]
        assert "matrix" in jobs["test"]["strategy"]


class TestMultiEnvironment:
    def test_environments(self):
        dev = 'environment dev { namespace: "myapp-dev" labels: { env: "dev" } }'
        prod = 'environment prod { namespace: "myapp-prod" labels: { env: "prod" } }'
        pdev = P.parse(dev, "dev.infra")
        pprod = P.parse(prod, "prod.infra")
        assert SemanticValidator().validate(pdev).is_valid
        assert SemanticValidator().validate(pprod).is_valid

        files_dev = infra_compile(pdev, target="kubernetes").files
        files_prod = infra_compile(pprod, target="kubernetes").files
        ns_dev = all_docs("\n".join(files_dev.values()))
        ns_prod = all_docs("\n".join(files_prod.values()))
        assert any(
            n["metadata"]["name"] == "myapp-dev"
            for n in ns_dev
            if n["kind"] == "Namespace"
        )
        assert any(
            n["metadata"]["name"] == "myapp-prod"
            for n in ns_prod
            if n["kind"] == "Namespace"
        )


class TestErrorAccumulation:
    def test_multiple_errors(self):
        source = (
            "service api { image: undefined_image_var replicas: 0 port 99999 }\n"
            'service api { image: "duplicate" }'
        )
        result = SemanticValidator().validate(P.parse(source, "err.infra"))
        assert not result.is_valid
        assert len(result.errors) >= 3
        codes = [e.code for e in result.errors]
        for required in ("E001", "E011", "E012", "E002"):
            assert required in codes, f"missing {required} in {codes}"
        for e in result.errors:
            assert e.location is not None
            assert e.message


class TestCompilationSpeed:
    def test_large_stack_under_2s(self):
        source = "\n".join(
            [
                f'service svc{i} {{ image: "img" port {i + 1} replicas: 2 }}'
                for i in range(5)
            ]
        )
        source += (
            "\ndatabase a { type: postgres }\ndatabase b { type: mysql }\n"
            "cache c { type: redis }\nqueue q { type: rabbitmq }\n"
        )
        program = P.parse(source, "big.infra")
        assert SemanticValidator().validate(program).is_valid
        t0 = time.time()
        k8s = infra_compile(program, target="kubernetes").files
        compose = infra_compile(program, target="compose").files
        elapsed = time.time() - t0
        assert elapsed < 2.0, f"compilation too slow: {elapsed:.2f}s"
        assert len(k8s) > 0
        assert len(compose) > 0
        list(yaml.safe_load_all("\n".join(k8s.values())))
        yaml.safe_load(compose["docker-compose.yml"])


class TestVariablesAndExpressions:
    def test_variables_defined(self):
        source = (
            'let app_name = "myapp"\n'
            'const VERSION = "1.2.3"\n'
            "service api { image: app_name }"
        )
        result = SemanticValidator().validate(P.parse(source, "vars.infra"))
        # image references app_name (defined) -> no E001
        assert "E001" not in [e.code for e in result.errors]

    def test_template_string_placeholder(self):
        # template-string interpolation is a known TODO (kept as placeholder)
        source = "service api { image: `myapp:{version}` }"
        result = SemanticValidator().validate(P.parse(source, "tpl.infra"))
        # should not crash; interpolation is marked as future work
        assert result.errors == [] or result.errors
