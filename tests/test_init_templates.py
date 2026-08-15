"""Session 10 - Init templates (Zadanie 2)."""

from __future__ import annotations

import os

from typer.testing import CliRunner

from infra import parse, validate
from infra.cli.main import app

runner = CliRunner()


def _init(tmp_path, name: str, template: str):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(app, ["init", name, "--template", template, "--yes"])
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(cwd)
    return tmp_path / name


def _infra_files(root) -> list:
    return sorted((root / "infra").rglob("*.infra"))


def _all_valid(root) -> bool:
    for f in _infra_files(root):
        res = validate(parse(f.read_text()))
        if not res.is_valid:
            return False
    return True


class TestInitBasic:
    def test_creates_files_that_parse(self, tmp_path):
        root = _init(tmp_path, "myproj", "basic")
        files = _infra_files(root)
        assert len(files) >= 4  # main + service + database + secret
        for f in files:
            parse(f.read_text())  # must not raise

    def test_validates_clean(self, tmp_path):
        root = _init(tmp_path, "myproj", "basic")
        assert _all_valid(root)

    def test_contains_service_and_database(self, tmp_path):
        root = _init(tmp_path, "myproj", "basic")
        svc = (root / "infra" / "services" / "api.infra").read_text()
        db = (root / "infra" / "databases" / "main.infra").read_text()
        assert "service api" in svc
        assert "port: 8080" in svc
        assert "health http" in svc
        assert "database db" in db
        assert "backup" in db


class TestInitMicroservices:
    def test_creates_three_services(self, tmp_path):
        root = _init(tmp_path, "micro", "microservices")
        services_dir = root / "infra" / "services"
        assert len(list(services_dir.glob("*.infra"))) == 3
        content = "".join(f.read_text() for f in services_dir.glob("*.infra"))
        for svc in ("api", "worker", "frontend"):
            assert f"service {svc}" in content

    def test_includes_db_cache_queue(self, tmp_path):
        root = _init(tmp_path, "micro", "microservices")
        db = (root / "infra" / "databases" / "main.infra").read_text()
        cache = (root / "infra" / "caches" / "session.infra").read_text()
        queue = (root / "infra" / "queues" / "events.infra").read_text()
        assert "database db" in db
        assert "cache session" in cache
        assert "queue events" in queue

    def test_network_policies_present(self, tmp_path):
        root = _init(tmp_path, "micro", "microservices")
        api = (root / "infra" / "services" / "api.infra").read_text()
        assert "network_policy" in api
        assert "deny_from" in api

    def test_all_files_parse_and_validate(self, tmp_path):
        root = _init(tmp_path, "micro", "microservices")
        assert _all_valid(root)


class TestInitErrors:
    def test_unknown_template_fails(self, tmp_path):
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = runner.invoke(app, ["init", "x", "--template", "nope", "--yes"])
            assert result.exit_code == 1
        finally:
            os.chdir(cwd)

    def test_existing_directory_fails(self, tmp_path):
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            (tmp_path / "existing").mkdir()
            result = runner.invoke(app, ["init", "existing", "--yes"])
            assert result.exit_code == 1
        finally:
            os.chdir(cwd)
