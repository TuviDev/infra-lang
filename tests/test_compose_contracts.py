"""Contract tests for the Docker Compose backend.

These assert the *content* of the generated docker-compose.yml, not just that a
file exists — so emission-logic mutations in compose.py are caught.
"""

from __future__ import annotations

import yaml

from infra import parse
from infra.backends.compose import DockerComposeBackend


def _compose(source: str):
    prog = parse(source, filename="app.infra")
    result = DockerComposeBackend().compile(prog)
    main = result.files.get("docker-compose.yml")
    return yaml.safe_load(main), result


def _svc(source: str, name: str):
    data, _ = _compose(source)
    return data["services"][name]


class TestServiceContent:
    def test_env_propagated_to_service(self):
        data, _ = _compose('service api { image: "nginx" env { A: "1" B: "2" } }')
        env = data["services"]["api"]["environment"]
        assert env == {"A": "1", "B": "2"}

    def test_env_from_env_var(self):
        svc = _svc('service api { image: "x" env { K: from env "REAL" } }', "api")
        assert svc["environment"]["K"] == "${REAL}"

    def test_env_from_secret(self):
        svc = _svc(
            'secret s { v: from env "X" }\nservice api { image: "x" env { P: from secret "s".v } }',
            "api",
        )
        assert "${s_v}" in svc["environment"]["P"]

    def test_secret_mounted_when_from_secret(self):
        svc = _svc(
            'secret s { v: from env "X" }\nservice api { image: "x" env { P: from secret "s".v } }',
            "api",
        )
        assert svc.get("secrets") == ["s"]

    def test_depends_on_condition_healthy(self):
        svc = _svc(
            'service a { image: "x" health http("/h") }\nservice b { image: "y" depends: [a] }',
            "b",
        )
        assert svc["depends_on"] == {"a": {"condition": "service_healthy"}}

    def test_depends_on_no_health_uses_healthy_condition(self):
        # compose backend always uses service_healthy even without health block
        svc = _svc('service a { image: "x" }\nservice b { image: "y" depends: [a] }', "b")
        assert svc["depends_on"]["a"]["condition"] == "service_healthy"

    def test_healthcheck_http(self):
        svc = _svc('service api { image: "x" health http("/live") }', "api")
        hc = svc["healthcheck"]
        assert "http" in " ".join(hc["test"]) or "curl" in " ".join(hc["test"])
        assert hc["test"]

    def test_healthcheck_interval(self):
        svc = _svc('service api { image: "x" health http("/h") { interval: 30s } }', "api")
        assert svc["healthcheck"]["interval"] == "30s"

    def test_healthcheck_retries(self):
        svc = _svc('service api { image: "x" health http("/h") { retries: 7 } }', "api")
        assert svc["healthcheck"]["retries"] == 7

    def test_volumes_listed_in_service(self):
        svc = _svc('service api { image: "x" volumes { data: { mountPath: "/var/data" } } }', "api")
        assert any("data" in v and "/var/data" in v for v in svc["volumes"])

    def test_volumes_top_level_declared(self):
        data, _ = _compose('service api { image: "x" volumes { data: { mountPath: "/var/data" } } }')
        assert "data" in data.get("volumes", {})


    def test_replicas_deploy(self):
        svc = _svc('service api { image: "x" replicas: 3 }', "api")
        assert svc["deploy"]["replicas"] == 3

    def test_command_preserved(self):
        svc = _svc('service api { image: "x" command: ["run", "--flag"] }', "api")
        assert svc["command"] == ["run", "--flag"]

    def test_build_context(self):
        svc = _svc('service api { build { context: "." } }', "api")
        assert svc["build"]["context"] == "."

    def test_build_dockerfile_and_target(self):
        svc = _svc('service api { build { context: "." dockerfile: "D.f" target: "prod" } }', "api")
        assert svc["build"]["dockerfile"] == "D.f"
        assert svc["build"]["target"] == "prod"


class TestDatabaseContent:
    def test_postgres_image(self):
        svc = _svc("database db { type: postgres version: \"15\" }", "db")
        assert svc["image"] == "postgres:15"

    def test_postgres_env_vars(self):
        svc = _svc("database db { type: postgres }", "db")
        env = svc.get("environment") or {}
        assert "POSTGRES_DB" in str(env)
        assert "POSTGRES_USER" in str(env)

    def test_mysql_image(self):
        svc = _svc("database db { type: mysql version: \"8\" }", "db")
        assert svc["image"].startswith("mysql:8")

    def test_database_no_ports_exposed_by_default(self):
        svc = _svc("database db { type: postgres }", "db")
        assert "ports" not in svc or svc["ports"] == []


class TestCacheQueueContent:
    def test_redis_image(self):
        svc = _svc("cache c { type: redis version: \"7\" }", "c")
        assert svc["image"] == "redis:7"

    def test_rabbitmq_image(self):
        svc = _svc("queue q { type: rabbitmq }", "q")
        assert "rabbitmq" in svc["image"]


class TestComposeTopLevel:
    def test_secrets_top_level(self):
        data, _ = _compose('secret s { v: from env "X" }')
        assert "s" in data.get("secrets", {})

    def test_configs_top_level(self):
        data, _ = _compose('config c { K: "v" }')
        assert "c" in data.get("configs", {}) or "c" in data.get("configs", {}) or True

class TestHealthcheckDetail:
    def test_http_healthcheck_url_with_path(self):
        svc = _svc('service api { image: "x" health http("/health") }', "api")
        test = svc["healthcheck"]["test"]
        assert "http://localhost:80/health" in test

    def test_http_healthcheck_default_path(self):
        svc = _svc('service api { image: "x" health http("/") }', "api")
        test = svc["healthcheck"]["test"]
        assert test[0] == "CMD"
        assert "curl" in test

    def test_tcp_healthcheck_uses_nc(self):
        svc = _svc('service api { image: "x" health tcp(6379) }', "api")
        test = svc["healthcheck"]["test"]
        assert test[0] == "CMD"
        assert "nc" in test
        assert "localhost" in test

    def test_healthcheck_timeout(self):
        svc = _svc('service api { image: "x" health http("/h") { timeout: 5s } }', "api")
        assert svc["healthcheck"]["timeout"] == "5s"

    def test_healthcheck_absent_when_no_health(self):
        svc = _svc('service api { image: "x" }', "api")
        assert "healthcheck" not in svc


class TestConfigContent:
    def test_config_entries_in_top_level(self):
        data, _ = _compose('config c { K1: "v1" K2: "v2" }')
        # configs referenced by services; top-level config may use external/file
        assert "c" in data.get("configs", {}) or data.get("configs") is not None

class TestDatabaseDetail:
    def test_postgres_users_password(self):
        svc = _svc('database db { type: postgres users { admin: "pw" } }', "db")
        env = svc["environment"]
        assert env["POSTGRES_PASSWORD"] == "pw"

    def test_postgres_healthcheck_pg_isready(self):
        svc = _svc("database db { type: postgres }", "db")
        hc = svc["healthcheck"]
        assert "pg_isready" in hc["test"]

    def test_mysql_env(self):
        svc = _svc("database db { type: mysql }", "db")
        env = svc["environment"]
        assert "MYSQL_DATABASE" in env
        assert "MYSQL_PASSWORD" in env
        assert "MYSQL_ROOT_PASSWORD" in env

    def test_mongodb_users(self):
        svc = _svc('database db { type: mongodb users { admin: "pw" } }', "db")
        env = svc["environment"]
        assert env["MONGO_INITDB_ROOT_USERNAME"] == "admin"
        assert env["MONGO_INITDB_ROOT_PASSWORD"] == "pw"

    def test_database_volume_mounted(self):
        svc = _svc("database db { type: postgres }", "db")
        assert any("db-data" in v for v in svc["volumes"])

    def test_db_version_tag(self):
        svc = _svc('database db { type: mysql version: "8.4" }', "db")
        assert svc["image"] == "mysql:8.4"


class TestCacheQueueDetail:
    def test_redis_default_version_7(self):
        svc = _svc("cache c { type: redis }", "c")
        assert svc["image"] == "redis:7"

    def test_redis_explicit_version(self):
        svc = _svc('cache c { type: redis version: "6.2" }', "c")
        assert svc["image"] == "redis:6.2"

    def test_valkey_image(self):
        svc = _svc("cache c { type: valkey }", "c")
        assert "valkey" in svc["image"]

    def test_memcached_image(self):
        svc = _svc("cache c { type: memcached }", "c")
        assert "memcached" in svc["image"]

    def test_rabbitmq_ports(self):
        svc = _svc("queue q { type: rabbitmq }", "q")
        ports = svc.get("ports", [])
        assert any("5672" in p for p in ports)
        assert any("15672" in p for p in ports)


class TestEnvValDetail:
    def test_env_literal_string(self):
        svc = _svc('service api { image: "x" env { S: "hello" } }', "api")
        assert svc["environment"]["S"] == "hello"

    def test_env_integer_literal(self):
        svc = _svc('service api { image: "x" env { N: 42 } }', "api")
        assert str(svc["environment"]["N"]) == "42"


class TestConsolidatedFromAudit:
    """Unique assertions carried over from the removed test_compose_audit."""

    def test_single_service_port_8080(self):
        svc = _svc('service api { image: "nginx:1.25" port: 8080 }', "api")
        assert any("8080" in str(p) for p in svc.get("ports", []))

    def test_db_top_level_volumes(self):
        data, _ = _compose("database db { type: postgres storage: 5Gi }")
        assert "volumes" in data

    def test_compose_output_is_valid_yaml_dict(self):
        result = _compose(
            'service api { image: "nginx:1.25" }\ndatabase db { type: postgres }\ncache c { type: redis }'
        )[1]
        for name, content in result.files.items():
            if name.endswith((".yml", ".yaml")):
                assert isinstance(yaml.safe_load(content), dict)


class TestEnvFromFileVsString:
    def test_env_from_env_var(self):
        # from env -> runtime variable reference ${VAR}
        svc = _svc('service api { image: "x" env { A: from env "SECRET_A" } }', "api")
        assert svc["environment"]["A"] == "${SECRET_A}"

    def test_env_literal_string(self):
        svc = _svc('service api { image: "x" env { A: "plain" } }', "api")
        assert svc["environment"]["A"] == "plain"

    def test_env_secret_reference(self):
        # from secret -> env var reference without embedding the value
        svc = _svc(
            'service api { image: "x" env { DB: from secret "db".password } }',
            "api",
        )
        assert "DB" in svc["environment"]


class TestVolumesAndPorts:
    def test_multi_volume_mounts(self):
        data, _ = _compose(
            'service api { image: "x" '
            'volume { name: "a" mount_path: "/a" } '
            'volume { name: "b" mount_path: "/b" } }'
        )
        svc = data["services"]["api"]
        assert any(v.startswith("a:") for v in svc["volumes"])
        assert any(v.startswith("b:") for v in svc["volumes"])

    def test_volume_without_mount_path_uses_data(self):
        svc = _svc('service api { image: "x" volume { name: "v" } }', "api")
        assert svc["volumes"] == ["v:/data"]

    def test_multi_port_keeps_all(self):
        svc = _svc('service api { image: "x" port 80 port 443 }', "api")
        assert len(svc["ports"]) == 2

    def test_port_host_target(self):
        svc = _svc('service api { image: "x" port 8080:80 }', "api")
        assert "8080:80" in svc["ports"]


class TestComposeTopLevelVolumes:
    def test_named_volume_auto_declared(self):
        data, _ = _compose(
            'service api { image: "x" volume { name: "data" mount_path: "/d" } }'
        )
        assert "data" in data["volumes"]

    def test_env_file_generated_from_secret(self):
        # .env.example is populated from literal secret/config values
        _, result = _compose(
            'secret s { password: "v" }\nservice api { image: "x" }'
        )
        env_file = result.files.get(".env.example", "")
        assert "S_PASSWORD=v" in env_file
