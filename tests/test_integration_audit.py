"""Full integration tests covering realistic end-to-end user workflows."""

from __future__ import annotations

import yaml

from infra import parse, validate
from infra.backends.compose import DockerComposeBackend
from infra.backends.github import GitHubActionsBackend
from infra.backends.kubernetes import KubernetesBackend


class TestWebAppWorkflow:
    """Deploy a web app: service + database + cache + secret -> K8s resources."""

    SOURCE = """
    secret db-creds {
        url: from env "DATABASE_URL"
        password: from env "DB_PASSWORD"
    }

    database db {
        type: postgres
        version: "15"
        storage: 20Gi
        ssl: true
        backup {
            enabled: true
            schedule: "0 2 * * *"
            retention: 30d
        }
    }

    cache session {
        type: redis
        maxmemory: 512Mi
        persistence: true
    }

    service api {
        image: "myapp:v1.0.0"
        replicas: 3
        port { container: 8080 }
        ingress { host: "api.example.com" tls: true }
        env {
            DB_URL: from secret "db-creds".url
            REDIS_URL: "redis://session:6379"
        }
        health: http("/health")
        resources {
            requests { cpu: 200m, memory: 256Mi }
            limits   { cpu: 1000m, memory: 512Mi }
        }
        autoscale { min: 2, max: 10, target_cpu: 70 }
        depends: [db, session]
    }
    """

    def test_parse_without_error(self):
        parse(self.SOURCE)

    def test_validate_is_valid(self):
        r = validate(parse(self.SOURCE))
        semantic = [e for e in r.errors if not (e.code or "").startswith("SEC")]
        assert len(semantic) == 0

    def test_k8s_output_all_resources_present(self):
        docs = [
            d
            for d in yaml.safe_load_all(
                "\n".join(KubernetesBackend().compile(parse(self.SOURCE)).files.values())
            )
            if d
        ]
        kinds = {d["kind"] for d in docs}
        assert "Deployment" in kinds
        assert "StatefulSet" in kinds
        assert "Secret" in kinds
        assert "HorizontalPodAutoscaler" in kinds
        assert "Ingress" in kinds

    def test_k8s_output_all_valid_yaml(self):
        files = KubernetesBackend().compile(parse(self.SOURCE)).files
        for name, content in files.items():
            for doc in yaml.safe_load_all(content):
                assert doc is None or isinstance(doc, dict), f"Invalid YAML in {name}"

    def test_compose_output_valid_yaml(self):
        files = DockerComposeBackend().compile(parse(self.SOURCE)).files
        for name, content in files.items():
            if name.endswith((".yml", ".yaml")):
                assert isinstance(yaml.safe_load(content), dict)

    def test_no_undefined_variables(self):
        r = validate(parse(self.SOURCE))
        assert not [e for e in r.errors if e.code == "E001"]


class TestCICDWorkflow:
    """Create a CI/CD pipeline: test -> build -> deploy GitHub workflow."""

    SOURCE = """
    pipeline main {
        trigger {
            branches: ["main", "develop"]
            paths: ["src/**", "tests/**"]
        }
        stages {
            test: {
                runsOn: "ubuntu-latest"
                matrix { python: ["3.11", "3.12"] }
                steps {
                    checkout: { uses: "actions/checkout@v4" }
                    install: { run: "pip install -e .[dev]" }
                    runtests: { run: "pytest" }
                }
            }
            build: {
                needs: [test]
                runsOn: "ubuntu-latest"
                steps {
                    checkout: { uses: "actions/checkout@v4" }
                    build: { run: "docker build -t myapp ." }
                }
            }
            deploy: {
                needs: [build]
                runsOn: "ubuntu-latest"
                steps { deploy: { run: "kubectl apply -f k8s/" } }
            }
        }
    }
    """

    def test_parse_without_error(self):
        parse(self.SOURCE)

    def test_validate_is_valid(self):
        r = validate(parse(self.SOURCE))
        assert not any(e.code == "E006" for e in r.errors)

    def test_github_output_has_three_jobs(self):
        files = GitHubActionsBackend().compile(parse(self.SOURCE)).files
        data = yaml.safe_load(list(files.values())[0])
        assert len(data.get("jobs", {})) >= 3

    def test_github_output_valid_yaml(self):
        files = GitHubActionsBackend().compile(parse(self.SOURCE)).files
        for content in files.values():
            assert isinstance(yaml.safe_load(content), dict)


class TestVariableInterpolationWorkflow:
    """Use variables for DRY config with template interpolation."""

    SOURCE = """
    const ORG = "mycompany"
    const APP = "api"
    const VERSION = "v2.1.0"
    const TAG = `{ORG}/{APP}:{VERSION}`

    service api {
        image: TAG
        replicas: 3
    }
    """

    def test_parse_without_error(self):
        parse(self.SOURCE)

    def test_template_interpolated_in_k8s(self):
        files = KubernetesBackend().compile(parse(self.SOURCE)).files
        content = "\n".join(files.values())
        assert "mycompany/api:v2.1.0" in content


class TestImportWorkflow:
    """Split config across files with the import system."""

    def test_imported_const_available(self, tmp_path):
        (tmp_path / "lib.infra").write_text('const BASE_IMAGE = "myapp:v1.0"')
        (tmp_path / "main.infra").write_text(
            'import "./lib.infra"\nservice api { image: BASE_IMAGE }'
        )
        from infra.parser import parse_file

        result = validate(parse_file(tmp_path / "main.infra"))
        assert not [e for e in result.errors if e.code == "E001"]

    def test_compiled_image_from_import(self, tmp_path):
        (tmp_path / "ver.infra").write_text('const VER = "v99"')
        (tmp_path / "main.infra").write_text(
            'import "./ver.infra"\nservice s { image: `nginx:{VER}` }'
        )
        from infra.parser import parse_file

        files = KubernetesBackend().compile(parse_file(tmp_path / "main.infra")).files
        assert "nginx:v99" in "\n".join(files.values())
