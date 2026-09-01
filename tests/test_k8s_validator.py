"""Session 10 - Kubernetes output validation (Zadanie 6)."""

from __future__ import annotations

from typer.testing import CliRunner

from infra.cli.main import app
from infra.validation.k8s_validator import KubernetesOutputValidator

runner = CliRunner()

VALID = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: api
          image: nginx:1.0
"""


class TestValidatorUnit:
    def test_accepts_valid_yaml(self):
        issues = KubernetesOutputValidator().validate(VALID)
        assert issues == []

    def test_detects_missing_apiversion(self):
        issues = KubernetesOutputValidator().validate(
            "kind: Deployment\nmetadata: {name: api}\n"
        )
        assert any("apiVersion" in i.message for i in issues)
        assert all(i.severity == "error" for i in issues)

    def test_detects_missing_kind(self):
        issues = KubernetesOutputValidator().validate(
            "apiVersion: apps/v1\nmetadata: {name: api}\n"
        )
        assert any("kind" in i.message for i in issues)

    def test_detects_replicas_as_string(self):
        yaml = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\n"
            'spec:\n  replicas: "3"\n  template:\n    spec:\n'
            "      containers:\n        - name: api\n          image: x\n"
        )
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("replicas" in i.message for i in issues)

    def test_detects_bad_dns_name(self):
        yaml = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: MyApi}\n"
            "spec:\n  replicas: 1\n  template:\n    spec:\n"
            "      containers:\n        - name: api\n          image: x\n"
        )
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("DNS-1123" in i.message for i in issues)

    def test_detects_unparseable_yaml(self):
        issues = KubernetesOutputValidator().validate("not: [valid: yaml")
        assert issues
        assert any("parseable" in i.message for i in issues)

    def test_detects_service_port_as_string(self):
        yaml = (
            "apiVersion: v1\nkind: Service\nmetadata: {name: api}\n"
            'spec:\n  ports:\n    - port: "80"\n'
        )
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("Service port" in i.message for i in issues)

    def test_detects_service_no_ports(self):
        yaml = "apiVersion: v1\nkind: Service\nmetadata: {name: api}\nspec: {}\n"
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("no ports" in i.message for i in issues)

    def test_detects_hpa_bad_max_replicas(self):
        yaml = (
            "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\n"
            'metadata: {name: api}\nspec:\n  maxReplicas: "10"\n'
        )
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("maxReplicas" in i.message for i in issues)

    def test_detects_network_policy_missing_pod_selector(self):
        yaml = (
            "apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n"
            "metadata: {name: api-np}\nspec: {}\n"
        )
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("podSelector" in i.message for i in issues)

    def test_detects_resource_quota_missing_hard(self):
        yaml = (
            "apiVersion: v1\nkind: ResourceQuota\nmetadata: {name: ns-quota}\n"
            "spec: {}\n"
        )
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("spec.hard" in i.message for i in issues)

    def test_detects_non_mapping_document(self):
        issues = KubernetesOutputValidator().validate("- 1\n- 2\n")
        assert any("not a mapping" in i.message for i in issues)

    def test_has_errors(self):
        assert KubernetesOutputValidator().has_errors(VALID) is False
        assert (
            KubernetesOutputValidator().has_errors(
                "kind: Deployment\nmetadata: {name: api}\n"
            )
            is True
        )

    def test_detects_containers_not_list(self):
        yaml = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\n"
            "spec:\n  replicas: 1\n  template:\n    spec:\n"
            '      containers: "x"\n'
        )
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("containers must be a list" in i.message for i in issues)

    def test_detects_missing_metadata_name(self):
        yaml = "apiVersion: v1\nkind: Service\nspec:\n  ports:\n    - port: 80\n"
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("metadata.name" in i.message for i in issues)

    def test_detects_resource_limit_plain_number(self):
        yaml = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\n"
            "spec:\n  replicas: 1\n  template:\n    spec:\n"
            "      containers:\n        - name: api\n          image: x\n"
            "          resources:\n            limits:\n              cpu: 500\n"
        )
        issues = KubernetesOutputValidator().validate(yaml)
        assert any("plain number" in i.message for i in issues)

    def test_validate_files_returns_list(self):
        from infra.validation.k8s_validator import K8sValidationIssue

        v = KubernetesOutputValidator()
        issues = v.validate_files({"a.yaml": VALID, "b.yaml": "kind: X\n"})
        assert isinstance(issues, list)
        assert issues
        assert all(isinstance(i, K8sValidationIssue) for i in issues)
        assert any("b.yaml" in i.message for i in issues)

    def test_issue_fields(self):
        from infra.validation.k8s_validator import K8sValidationIssue

        issues = KubernetesOutputValidator().validate("kind: X\n")
        issue = next(i for i in issues if i.field == "apiVersion")
        assert isinstance(issue, K8sValidationIssue)
        assert issue.severity == "error"
        assert issue.field == "apiVersion"
        assert issue.message
        assert str(issue)


class TestCompileValidateOutput:
    def _compile(self, tmp_path, name: str, content: str):
        f = tmp_path / f"{name}.infra"
        f.write_text(content)
        out = tmp_path / f"out-{name}"
        result = runner.invoke(
            app, ["compile", str(f), "--validate-output", "--output", str(out)]
        )
        return result, out

    def test_valid_output_exit_0(self, tmp_path):
        result, out = self._compile(tmp_path, "good", 'service api { image: "x:1" }')
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_invalid_output_exit_1(self, tmp_path):
        result, _ = self._compile(tmp_path, "bad", 'service MyApi { image: "x:1" }')
        assert result.exit_code == 1
        assert "Validation failed" in result.output

    def test_no_validate_output_ignores(self, tmp_path):
        f = tmp_path / "bad.infra"
        f.write_text('service MyApi { image: "x:1" }')
        out = tmp_path / "out"
        result = runner.invoke(app, ["compile", str(f), "--output", str(out)])
        assert result.exit_code == 0  # no --validate-output, so no failure


class TestValidatorErrorPaths:
    """Targeted contracts for the previously untested error paths (v0.4.4).

    Maps to the audit table for `k8s_validator.py`: non-mapping documents,
    containers without an image, invalid resource quantities and the four
    kind-specific early-exit branches.
    """

    def _validate(self, text: str):
        return KubernetesOutputValidator().validate(text)

    def test_document_is_a_list_not_a_mapping(self):
        issues = self._validate("- []\n")
        assert len(issues) == 1
        assert issues[0].message == "document is not a mapping"
        assert issues[0].severity == "error"

    def test_document_is_a_scalar_not_a_mapping(self):
        issues = self._validate("just a plain string\n")
        assert any(i.message == "document is not a mapping" for i in issues)

    def test_container_missing_image(self):
        doc = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\n"
            "spec:\n  replicas: 1\n  template:\n    spec:\n"
            "      containers:\n        - name: api\n"
        )
        issues = self._validate(doc)
        assert any(i.message == "container is missing an image" for i in issues)
        assert any(i.field == "containers[].image" for i in issues)

    def test_invalid_resource_quantity_string(self):
        doc = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\n"
            "spec:\n  replicas: 1\n  template:\n    spec:\n"
            "      containers:\n        - name: api\n          image: nginx:1.25\n"
            '          resources:\n            limits:\n              cpu: "200x"\n'
        )
        issues = self._validate(doc)
        assert any(
            "'200x' is not a valid Kubernetes resource quantity" in i.message
            for i in issues
        )

    def test_service_port_must_be_integer(self):
        doc = (
            "apiVersion: v1\nkind: Service\nmetadata: {name: api}\n"
            'spec:\n  ports:\n    - port: "80"\n'
        )
        issues = self._validate(doc)
        assert any("Service port must be an integer" in i.message for i in issues)

    def test_hpa_max_replicas_must_be_integer(self):
        doc = (
            "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\n"
            'metadata: {name: api}\nspec:\n  maxReplicas: "3"\n'
        )
        issues = self._validate(doc)
        assert any("maxReplicas must be an integer" in i.message for i in issues)

    def test_network_policy_requires_pod_selector(self):
        doc = (
            "apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n"
            "metadata: {name: api}\nspec: {}\n"
        )
        issues = self._validate(doc)
        assert any("podSelector" in i.message for i in issues)

    def test_resource_quota_requires_hard(self):
        doc = "apiVersion: v1\nkind: ResourceQuota\nmetadata: {name: api}\nspec: {}\n"
        issues = self._validate(doc)
        assert any("spec.hard" in i.message for i in issues)


class TestValidatorEdgeBranches:
    """The remaining false-arcs and skips (v0.4.4 follow-up coverage)."""

    def _validate(self, text: str):
        return KubernetesOutputValidator().validate(text)

    def test_empty_documents_are_skipped(self):
        assert self._validate("") == []
        assert self._validate("---\n") == []

    def test_hpa_with_valid_max_replicas(self):
        doc = (
            "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\n"
            "metadata: {name: api}\nspec:\n  maxReplicas: 5\n"
        )
        issues = self._validate(doc)
        assert not any("maxReplicas" in i.message for i in issues)

    def test_network_policy_with_pod_selector(self):
        doc = (
            "apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n"
            "metadata: {name: api}\nspec:\n  podSelector: {}\n"
        )
        issues = self._validate(doc)
        assert not any("podSelector" in i.message for i in issues)

    def test_resource_quota_with_hard(self):
        doc = (
            "apiVersion: v1\nkind: ResourceQuota\nmetadata: {name: api}\n"
            'spec:\n  hard:\n    cpu: "10"\n'
        )
        issues = self._validate(doc)
        assert not any("spec.hard" in i.message for i in issues)

    def test_valid_quantity_strings_pass(self):
        doc = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\n"
            "spec:\n  replicas: 1\n  template:\n    spec:\n"
            "      containers:\n        - name: api\n          image: nginx:1.25\n"
            '          resources:\n            limits:\n              cpu: "500m"\n'
            '              memory: "256Mi"\n'
        )
        assert self._validate(doc) == []

    def test_unrecognized_kind_skips_kind_specific_checks(self):
        # Exercises the final `elif kind == "ResourceQuota"` -> false arc.
        doc = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: api}\nspec: {}\n"
        assert self._validate(doc) == []
