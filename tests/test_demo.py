"""Verify the examples/demo project parses, validates and compiles."""

from __future__ import annotations

from pathlib import Path

import yaml

from infra import parse, validate
from infra.backends import get_backend
from infra.parser import parse_file


class TestDemoProject:
    DEMO = Path("examples/demo")

    def test_demo_directory_exists(self):
        assert self.DEMO.exists()

    def test_main_infra_parses(self):
        f = self.DEMO / "main.infra"
        assert f.exists()
        parse(f.read_text(), filename=str(f))

    def test_demo_validates_without_semantic_errors(self):
        program = parse_file(self.DEMO / "main.infra")
        result = validate(program)
        non_sec = [e for e in result.errors if not (e.code or "").startswith("SEC")]
        assert len(non_sec) == 0, f"Semantic errors: {[e.message for e in non_sec]}"

    def test_demo_compiles_to_valid_k8s_yaml(self):
        program = parse_file(self.DEMO / "main.infra")
        result = get_backend("kubernetes").compile(program)
        for fname, content in result.files.items():
            for doc in yaml.safe_load_all(content):
                assert doc is None or isinstance(doc, dict), fname

    def test_demo_compiles_to_valid_compose_yaml(self):
        program = parse_file(self.DEMO / "main.infra")
        result = get_backend("compose").compile(program)
        for fname, content in result.files.items():
            if fname.endswith((".yml", ".yaml")):
                data = yaml.safe_load(content)
                assert data is None or isinstance(data, dict), fname

    def test_demo_readme_exists(self):
        assert (self.DEMO / "README.md").exists()

    def test_demo_contains_service_database_and_cache(self):
        api = (self.DEMO / "api.infra").read_text()
        db = (self.DEMO / "databases.infra").read_text()
        cache = (self.DEMO / "cache.infra").read_text()
        assert "service api" in api
        assert "database db" in db
        assert "cache session" in cache
