"""Backend compilation tests."""

from __future__ import annotations

import yaml

from infra import compile as infra_compile
from infra import parse


def parse_and_compile(source: str, backend_name: str, **opts) -> dict:
    """Parse, compile and return a filename -> content dict."""
    program = parse(source)
    result = infra_compile(program, target=backend_name, **opts)
    return result.files


def load_yaml(content: str) -> dict:
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError as e:  # pragma: no cover
        raise AssertionError(f"Invalid YAML: {e}\n{content}")


def load_all_yaml(content: str) -> list:
    return [d for d in yaml.safe_load_all(content) if d]


# --------------------------------------------------------------------------- #
# Kubernetes
# --------------------------------------------------------------------------- #


class TestKubernetesBackend:
    def test_service_generates_deployment(self):
        files = parse_and_compile(
            'service api { image: "myapp:latest" port: 8080 replicas: 3 }', "kubernetes"
        )
        content = "\n".join(files.values())
        kinds = [d["kind"] for d in load_all_yaml(content)]
        assert "Deployment" in kinds
        assert "Service" in kinds

    def test_deployment_correct_replicas(self):
        files = parse_and_compile(
            'service api { image: "nginx" replicas: 5 }', "kubernetes"
        )
        content = "\n".join(files.values())
        deployment = next(
            d for d in load_all_yaml(content) if d["kind"] == "Deployment"
        )
        assert deployment["spec"]["replicas"] == 5

    def test_deployment_correct_image(self):
        files = parse_and_compile('service api { image: "myapp:v1.2.3" }', "kubernetes")
        content = "\n".join(files.values())
        deployment = next(
            d for d in load_all_yaml(content) if d["kind"] == "Deployment"
        )
        assert (
            deployment["spec"]["template"]["spec"]["containers"][0]["image"]
            == "myapp:v1.2.3"
        )

    def test_service_with_ingress(self):
        files = parse_and_compile(
            'service api { image: "nginx" port 80 ingress { host: "api.example.com" '
            'tls: true } }',
            "kubernetes",
        )
        content = "\n".join(files.values())
        kinds = [d["kind"] for d in load_all_yaml(content)]
        assert "Ingress" in kinds

    def test_database_generates_statefulset(self):
        files = parse_and_compile(
            "database db { type: postgres size: 10Gi }", "kubernetes"
        )
        content = "\n".join(files.values())
        kinds = [d["kind"] for d in load_all_yaml(content)]
        assert "StatefulSet" in kinds

    def test_secret_generates_k8s_secret(self):
        files = parse_and_compile(
            'secret db-creds { password: "supersecret" }', "kubernetes"
        )
        content = "\n".join(files.values())
        kinds = [d["kind"] for d in load_all_yaml(content)]
        assert "Secret" in kinds

    def test_config_generates_configmap(self):
        files = parse_and_compile(
            'config app { LOG_LEVEL: "info" MAX_CONN: 100 }', "kubernetes"
        )
        content = "\n".join(files.values())
        kinds = [d["kind"] for d in load_all_yaml(content)]
        assert "ConfigMap" in kinds

    def test_managed_by_label_present(self):
        files = parse_and_compile('service api { image: "nginx" }', "kubernetes")
        content = "\n".join(files.values())
        assert "managed-by" in content

    def test_output_is_valid_yaml(self):
        files = parse_and_compile(
            'service api { image: "nginx" replicas: 2 }\n'
            "database db { type: postgres size: 5Gi }\n"
            "cache c { type: redis maxmemory: 256Mi }",
            "kubernetes",
        )
        for filename, content in files.items():
            docs = list(yaml.safe_load_all(content))
            assert all(d is None or isinstance(d, dict) for d in docs), (
                f"Bad YAML in {filename}"
            )

    def test_split_mode_multiple_files(self):
        files = parse_and_compile(
            'service api { image: "nginx" }\nservice worker { image: "w" }',
            "kubernetes",
            split=True,
        )
        assert len(files) >= 2

    def test_resources_mapping(self):
        files = parse_and_compile(
            'service api { image: "nginx" resources { cpu: 500m memory: 128Mi } }',
            "kubernetes",
        )
        content = "\n".join(files.values())
        assert "500m" in content
        assert "128Mi" in content


# --------------------------------------------------------------------------- #
# Docker Compose
# --------------------------------------------------------------------------- #


class TestDockerComposeBackend:
    def test_service_in_compose(self):
        files = parse_and_compile('service api { image: "nginx:latest" }', "compose")
        data = load_yaml(files["docker-compose.yml"])
        assert "services" in data
        assert "api" in data["services"]

    def test_service_image_correct(self):
        files = parse_and_compile('service api { image: "myapp:v2" }', "compose")
        data = load_yaml(files["docker-compose.yml"])
        assert data["services"]["api"]["image"] == "myapp:v2"

    def test_ports_mapping(self):
        files = parse_and_compile(
            'service api { image: "nginx" port: 8080 }', "compose"
        )
        data = load_yaml(files["docker-compose.yml"])
        ports = data["services"]["api"].get("ports", [])
        assert any("8080" in str(p) for p in ports)

    def test_env_vars_present(self):
        files = parse_and_compile(
            'service api { image: "nginx" env { DEBUG: "true" PORT: "8080" } }',
            "compose",
        )
        data = load_yaml(files["docker-compose.yml"])
        env = data["services"]["api"].get("environment", {})
        env_str = str(env)
        assert "DEBUG" in env_str

    def test_database_postgres_image(self):
        files = parse_and_compile(
            'database db { type: postgres version: "15" }', "compose"
        )
        data = load_yaml(files["docker-compose.yml"])
        assert "postgres" in data["services"]["db"]["image"]

    def test_database_env_vars(self):
        files = parse_and_compile("database db { type: postgres }", "compose")
        data = load_yaml(files["docker-compose.yml"])
        assert "POSTGRES" in str(data["services"]["db"].get("environment", {}))

    def test_depends_on_mapping(self):
        files = parse_and_compile(
            'service db { image: "postgres" }\nservice api { image: "myapp" depends: '
            '["db"] }',
            "compose",
        )
        data = load_yaml(files["docker-compose.yml"])
        assert "db" in str(data["services"]["api"].get("depends_on", {}))

    def test_env_example_generated(self):
        files = parse_and_compile('secret db-pass { password: "secret" }', "compose")
        assert any(".env" in f for f in files.keys())

    def test_output_is_valid_yaml(self):
        files = parse_and_compile(
            'service api { image: "nginx" }\ndatabase db { type: postgres }\ncache c { '
            'type: redis }',
            "compose",
        )
        for filename, content in files.items():
            if filename.endswith((".yml", ".yaml")):
                load_yaml(content)


# --------------------------------------------------------------------------- #
# Terraform
# --------------------------------------------------------------------------- #


class TestTerraformBackend:
    def test_cluster_generates_tf(self):
        files = parse_and_compile(
            'cluster main { provider: aws region: "eu-west-1" version: "1.28" }',
            "terraform",
        )
        assert any(".tf" in f for f in files.keys())

    def test_required_files_exist(self):
        files = parse_and_compile("cluster main { provider: aws }", "terraform")
        names = " ".join(files.keys())
        assert "main" in names
        assert "variables" in names
        assert "outputs" in names

    def test_managed_by_tag(self):
        files = parse_and_compile("cluster main { provider: aws }", "terraform")
        content = "\n".join(files.values())
        assert "infra-lang" in content

    def test_database_rds(self):
        files = parse_and_compile(
            "database db { type: postgres size: 20Gi }", "terraform"
        )
        content = "\n".join(files.values())
        assert "aws_db_instance" in content

    def test_s3_bucket(self):
        files = parse_and_compile(
            'storage assets { type: s3 bucket: "my-assets" region: "us-east-1" }',
            "terraform",
        )
        content = "\n".join(files.values())
        assert "aws_s3_bucket" in content

    def test_secret_terraform(self):
        files = parse_and_compile(
            'secret creds { api_key: from vault "x" }', "terraform"
        )
        content = "\n".join(files.values())
        assert "aws_secretsmanager_secret" in content


# --------------------------------------------------------------------------- #
# GitHub Actions
# --------------------------------------------------------------------------- #


class TestGitHubActionsBackend:
    @staticmethod
    def _workflow(files) -> dict:
        # GitHub uses YAML 1.2 where `on` is a string; PyYAML 1.1 loads it as True.
        data = load_yaml([c for k, c in files.items() if k != "dependabot.yml"][0])
        return data.get("on") or data.get(True)

    def test_pipeline_generates_workflow(self):
        files = parse_and_compile(
            'pipeline ci { trigger { branches: ["main"] } '
            'stages { test: { runsOn: "ubuntu-latest" steps { s: { run: "echo hi" } } '
            '} '
            '} }',
            "github",
        )
        assert len(files) > 0
        data = load_yaml([c for k, c in files.items() if k != "dependabot.yml"][0])
        assert data.get("on") is not None or data.get(True) is not None
        assert "jobs" in data

    def test_trigger_branches(self):
        files = parse_and_compile(
            'pipeline ci { trigger { branches: ["main", "develop"] } stages { t: { '
            'steps { s: { run: "x" } } } } }',
            "github",
        )
        on = self._workflow(files)
        assert "push" in on or "pull_request" in on

    def test_matrix(self):
        files = parse_and_compile(
            'pipeline ci { stages { test: { runsOn: "ubuntu-latest" '
            'matrix { python: ["3.10", "3.11", "3.12"] } steps { s: { run: "pytest" } '
            '} '
            '} } }',
            "github",
        )
        data = load_yaml(list(files.values())[0])
        job = list(data["jobs"].values())[0]
        assert "matrix" in job["strategy"]

    def test_steps_run(self):
        files = parse_and_compile(
            'pipeline ci { stages { build: { runsOn: "ubuntu-latest" '
            'steps { a: { run: "pip install ." } b: { run: "pytest" } } } } }',
            "github",
        )
        data = load_yaml(list(files.values())[0])
        job = list(data["jobs"].values())[0]
        run_steps = [s for s in job["steps"] if "run" in s]
        assert len(run_steps) >= 2

    def test_uses_action(self):
        files = parse_and_compile(
            'pipeline ci { stages { build: { runsOn: "ubuntu-latest" steps { c: { '
            'uses: '
            '"actions/checkout@v4" } } } } }',
            "github",
        )
        content = list(files.values())[0]
        assert "actions/checkout" in content

    def test_output_valid_yaml(self):
        files = parse_and_compile(
            'pipeline ci { trigger { branches: ["main"] } stages { t: { runsOn: '
            '"ubuntu-latest" steps { s: { run: "echo ok" } } } } }',
            "github",
        )
        for content in files.values():
            load_yaml(content)


class TestSharedImageMaps:
    """The cache/queue image maps must come from one shared source so adding
    an engine type updates both Kubernetes and Compose together (no drift)."""

    def test_backends_share_same_cache_map(self):
        import infra.backends.compose as c
        import infra.backends.kubernetes as k

        assert k._CACHE_IMAGES is c._CACHE_IMAGES

    def test_backends_share_same_queue_map(self):
        import infra.backends.compose as c
        import infra.backends.kubernetes as k

        assert k._QUEUE_IMAGES is c._QUEUE_IMAGES

    def test_shared_maps_have_expected_engines(self):
        from infra.backends._images import CACHE_IMAGES, QUEUE_IMAGES

        assert set(CACHE_IMAGES) == {"redis", "valkey", "memcached"}
        assert set(QUEUE_IMAGES) == {"rabbitmq", "kafka", "nats"}

    def test_cache_engine_resolves_in_both_backends(self):
        # A new engine added to the shared map would be picked up by both.
        import infra.backends.compose as c
        import infra.backends.kubernetes as k

        for engine in ("redis", "valkey", "memcached"):
            kimg = k._CACHE_IMAGES.get(engine, "redis")
            cimg = c._CACHE_IMAGES.get(engine, "redis")
            assert kimg == cimg == engine

    def test_queue_engine_resolves_in_both_backends(self):
        import infra.backends.compose as c
        import infra.backends.kubernetes as k

        for engine, expected in (
            ("rabbitmq", "rabbitmq:3-management"),
            ("kafka", "bitnami/kafka"),
            ("nats", "nats"),
        ):
            assert k._QUEUE_IMAGES.get(engine) == expected
            assert c._QUEUE_IMAGES.get(engine) == expected


# --------------------------------------------------------------------------- #
# AUTO-GENERATED header
# --------------------------------------------------------------------------- #


class TestGeneratedHeader:
    """Every backend's output must start with the AUTO-GENERATED header."""

    BACKENDS = {
        "kubernetes": "infra.yaml",
        "compose": "docker-compose.yml",
        "terraform": "main.tf",
        "github": None,  # only present when a pipeline exists
        "helm": None,  # chart files; check Chart.yaml
    }

    def test_all_backends_start_with_autogenerated_header(self):
        source = 'service api { image: "nginx:1.25" port 80 }'
        for backend, _ in self.BACKENDS.items():
            files = parse_and_compile(source, backend)
            assert files, f"{backend} produced no files"
            first_content = next(iter(files.values()))
            assert first_content.startswith("# AUTO-GENERATED by infra-lang"), (
                f"{backend} missing header"
            )

    def test_header_includes_version_and_source(self):
        source = 'service api { image: "nginx:1.25" port 80 }'
        files = parse_and_compile(source, "kubernetes")
        head = files["infra.yaml"].splitlines()[:3]
        joined = "\n".join(head)
        assert "v0.7.1" in joined
        assert "# Source:" in joined
        assert "# Regenerate: infra compile" in joined

    def test_terraform_all_files_have_header(self):
        source = 'service api { image: "nginx:1.25" port 80 }'
        files = parse_and_compile(source, "terraform")
        for name, content in files.items():
            assert content.startswith("# AUTO-GENERATED by infra-lang"), (
                f"{name} missing header"
            )

    def test_github_pipeline_has_header(self):
        source = 'pipeline deploy { stages { t: { runsOn: "ubuntu" } } }'
        files = parse_and_compile(source, "github")
        assert files
        for name, content in files.items():
            assert content.startswith("# AUTO-GENERATED by infra-lang"), (
                f"{name} missing header"
            )
