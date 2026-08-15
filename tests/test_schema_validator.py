"""Tests for Kubernetes schema validation (structural JSON-schema checks)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from infra.backends.kubernetes import KubernetesBackend
from infra.cli.main import app
from infra.validation.schema_validator import (
    validate_compiled_output,
    validate_yaml_content,
)

runner = CliRunner()


def _errors(issues):
    return [i for i in issues if i.severity == "error"]


class TestSchemaValidator:
    def test_valid_deployment_no_issues(self):
        yaml_content = dedent(
            """
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: api
            spec:
              replicas: 2
              selector:
                matchLabels: {app: api}
              template:
                metadata:
                  labels: {app: api}
                spec:
                  containers:
                  - name: api
                    image: nginx:1.25
            """
        )
        issues = validate_yaml_content(yaml_content)
        assert len(_errors(issues)) == 0

    def test_deployment_wrong_api_version_warning(self):
        yaml_content = dedent(
            """
            apiVersion: apps/v1beta1
            kind: Deployment
            metadata:
              name: api
            spec:
              replicas: 2
            """
        )
        issues = validate_yaml_content(yaml_content)
        assert any(
            i.severity == "warning" and "apiVersion" in i.field for i in issues
        )

    def test_missing_name_is_error(self):
        yaml_content = dedent(
            """
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              labels: {app: api}
            spec:
              replicas: 2
            """
        )
        issues = validate_yaml_content(yaml_content)
        assert any(i.severity == "error" and "name" in i.field for i in issues)

    def test_replicas_as_string_is_error(self):
        yaml_content = dedent(
            """
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: api
            spec:
              replicas: "3"
              selector:
                matchLabels: {app: api}
            """
        )
        issues = validate_yaml_content(yaml_content)
        assert any(
            i.severity == "error" and "replicas" in i.field for i in issues
        )

    def test_hpa_requires_scale_target_ref(self):
        yaml_content = dedent(
            """
            apiVersion: autoscaling/v2
            kind: HorizontalPodAutoscaler
            metadata:
              name: api
            spec:
              minReplicas: 1
              maxReplicas: 5
            """
        )
        issues = validate_yaml_content(yaml_content)
        assert any(
            i.severity == "error" and "scaleTargetRef" in i.field for i in issues
        )

    def test_all_compiled_examples_pass_schema(self):
        from infra import parse

        for f in Path("examples").glob("*.infra"):
            program = parse(f.read_text())
            result = KubernetesBackend().compile(program)
            issues = validate_compiled_output(result.files)
            errors = _errors(issues)
            assert len(errors) == 0, (
                f"{f.name}: schema errors: "
                f"{[(i.kind, i.field, i.message) for i in errors]}"
            )

    def test_compile_validate_output_flag(self, tmp_path, infra_file):
        f = infra_file('service api { image: "nginx:1.25" }')
        out = tmp_path / "out"
        result = runner.invoke(
            app,
            ["compile", str(f), "--validate-output", "--output", str(out)],
        )
        assert result.exit_code == 0, result.output
