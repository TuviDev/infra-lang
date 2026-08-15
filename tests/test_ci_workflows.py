"""Verify the GitHub Actions workflows are well-formed."""

from __future__ import annotations

from pathlib import Path

import yaml

# In YAML 1.1 the `on:` key parses as the boolean True; GitHub YAML loaders
# treat it correctly, so we accept both spellings.
_TRIGGERS_KEY = "on"


def _triggers(data) -> dict:
    return data.get("on") or data.get(True) or {}


class TestCIWorkflows:
    def test_ci_yml_exists_and_parses(self):
        f = Path(".github/workflows/ci.yml")
        assert f.exists()
        data = yaml.safe_load(f.read_text())
        assert data is not None

    def test_ci_yml_triggers_on_push_and_pr(self):
        data = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
        triggers = _triggers(data)
        assert "push" in triggers
        assert "pull_request" in triggers

    def test_ci_yml_runs_pytest_with_coverage(self):
        content = Path(".github/workflows/ci.yml").read_text()
        assert "pytest" in content
        assert "cov" in content

    def test_ci_yml_runs_ruff_and_mypy(self):
        content = Path(".github/workflows/ci.yml").read_text()
        assert "ruff" in content
        assert "mypy" in content

    def test_ci_yml_matrix_has_multiple_python_versions(self):
        data = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
        matrix = data["jobs"]["test"]["strategy"]["matrix"]
        assert len(matrix["python-version"]) >= 2


class TestPublishWorkflow:
    def test_publish_yml_exists_and_parses(self):
        f = Path(".github/workflows/publish.yml")
        assert f.exists()
        data = yaml.safe_load(f.read_text())
        assert data is not None

    def test_publish_yml_triggers_on_version_tags(self):
        data = yaml.safe_load(Path(".github/workflows/publish.yml").read_text())
        on = _triggers(data)
        push = on.get("push", {})
        tags = push.get("tags", [])
        assert any("v" in t for t in tags)

    def test_publish_yml_uses_twine_and_build(self):
        content = Path(".github/workflows/publish.yml").read_text()
        assert "twine" in content
        assert "build" in content
