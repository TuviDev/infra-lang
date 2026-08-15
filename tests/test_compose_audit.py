"""Deep audit of the Docker Compose backend output."""

from __future__ import annotations

import yaml

from infra import parse
from infra.backends.compose import DockerComposeBackend


def compile_compose(source):
    result = DockerComposeBackend().compile(parse(source))
    main = (
        result.files.get("docker-compose.yml")
        or result.files.get("docker-compose.yaml")
        or next(iter(result.files.values()))
    )
    return yaml.safe_load(main)


class TestComposeServiceOutput:
    def test_service_in_services_section(self):
        data = compile_compose('service api { image: "nginx:1.25" }')
        assert "services" in data
        assert "api" in data["services"]

    def test_image_exact_match(self):
        image = "myapp:v1.2.3"
        data = compile_compose(f'service api {{ image: "{image}" }}')
        assert data["services"]["api"]["image"] == image

    def test_port_in_ports_list(self):
        data = compile_compose('service api { image: "nginx:1.25" port: 8080 }')
        ports = data["services"]["api"].get("ports", [])
        assert any("8080" in str(p) for p in ports)

    def test_env_var_present(self):
        data = compile_compose('service api { image: "nginx:1.25" env { LOG_LEVEL: "info" } }')
        env = data["services"]["api"].get("environment", {})
        assert "LOG_LEVEL" in str(env)

    def test_depends_on_present(self):
        source = (
            'service db { image: "postgres:15" }\n'
            'service api { image: "nginx:1.25" depends: [db] }'
        )
        data = compile_compose(source)
        deps = data["services"]["api"].get("depends_on", {})
        assert "db" in str(deps)

    def test_volumes_top_level_exists(self):
        data = compile_compose("database db { type: postgres storage: 5Gi }")
        assert "volumes" in data

    def test_postgres_env_vars_present(self):
        data = compile_compose("database db { type: postgres }")
        env = data["services"]["db"].get("environment", {})
        assert "POSTGRES" in str(env).upper()

    def test_redis_image_correct(self):
        data = compile_compose("cache c { type: redis maxmemory: 256Mi }")
        img = data["services"]["c"].get("image", "")
        assert "redis" in img.lower()

    def test_output_valid_yaml(self):
        source = (
            'service api { image: "nginx:1.25" }\n'
            "database db { type: postgres }\n"
            "cache c { type: redis }"
        )
        result = DockerComposeBackend().compile(parse(source))
        for name, content in result.files.items():
            if name.endswith((".yml", ".yaml")):
                assert isinstance(yaml.safe_load(content), dict)
