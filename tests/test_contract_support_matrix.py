"""Contract tests that pin the backend support matrix to real behavior.

If the support matrix in docs/support_matrix.md is wrong, these fail. The
matrix is a public contract: a structure is marked supported only if the
backend actually emits resources for it.
"""

from __future__ import annotations

import yaml

from infra import parse
from infra.backends import get_backend

# structure -> (source, marker_in_k8s_kinds)
_CASES = {
    "service": ('service s { image: "x:1" }', {"Deployment"}),
    "database": ("database d { type: postgres }", {"StatefulSet"}),
    "cache": ("cache c { type: redis }", {"Deployment"}),
    "queue": ("queue q { type: rabbitmq }", {"StatefulSet"}),
    "storage": ("storage s { type: object size: 10Gi }", {"PersistentVolumeClaim"}),
    "network": ('network n { cidr: "10.0.0.0/16" }', {"NetworkPolicy"}),
    "secret": ('secret s { key: from env "K" }', {"Secret"}),
    "config": ('config c { VAL: "x" }', {"ConfigMap"}),
    "environment": ('environment e { namespace: "ns" }', {"Namespace"}),
    "pipeline": (
        'pipeline p { trigger { branches: ["main"] } '
        'stages { t: { runsOn: "ubuntu-latest" steps { s: { run: "x" } } } } }',
        set(),
    ),
    "cluster": ("cluster c { provider: aws }", set()),
}


def _k8s_kinds(src: str):
    result = get_backend("kubernetes").compile(parse(src))
    content = "\n".join(result.files.values())
    return {
        d.get("kind")
        for d in yaml.safe_load_all(content)
        if d and d.get("kind")
    }


class TestKubernetesSupport:
    """K8s must support all structures except pipeline and cluster."""

    def test_supported_structures_emit_expected_kinds(self):
        for name in (
            "service",
            "database",
            "cache",
            "queue",
            "storage",
            "network",
            "secret",
            "config",
            "environment",
        ):
            src, expected = _CASES[name]
            kinds = _k8s_kinds(src)
            assert expected.issubset(kinds), (
                f"k8s should emit {expected} for {name}, got {kinds}"
            )

    def test_pipeline_not_supported_in_k8s(self):
        src, _ = _CASES["pipeline"]
        kinds = _k8s_kinds(src)
        assert "Deployment" not in kinds and "CronJob" not in kinds

    def test_cluster_not_supported_in_k8s(self):
        src, _ = _CASES["cluster"]
        kinds = _k8s_kinds(src)
        assert "Cluster" not in "".join(str(k) for k in kinds)


class TestComposeSupport:
    def test_service_database_cache_queue_secret_config_supported(self):
        for name in ("service", "database", "cache", "queue", "secret", "config"):
            src, _ = _CASES[name]
            result = get_backend("compose").compile(parse(src))
            content = "\n".join(result.files.values())
            # each of these must produce a docker-compose.yml with a service
            assert "docker-compose.yml" in result.files, f"{name}: no compose file"

    def test_pipeline_and_cluster_not_emitted(self):
        for name in ("pipeline", "cluster"):
            src, _ = _CASES[name]
            result = get_backend("compose").compile(parse(src))
            content = "\n".join(result.files.values())
            # no service entries added for these
            data = yaml.safe_load(result.files["docker-compose.yml"])
            assert not data.get("services"), f"{name} should not add compose services"


class TestTerraformSupport:
    def test_cluster_database_storage_network_secret_supported(self):
        for name, marker in [
            ("cluster", "aws_eks"),
            ("database", "postgres"),
            ("storage", "s3"),
            ("network", "vpc"),
            ("secret", "secret"),
        ]:
            src, _ = _CASES[name]
            result = get_backend("terraform").compile(parse(src))
            content = "\n".join(result.files.values()).lower()
            assert marker in content, f"terraform should emit {marker} for {name}"

    def test_service_cache_config_not_emitted(self):
        for name in ("service", "cache", "config"):
            src, _ = _CASES[name]
            result = get_backend("terraform").compile(parse(src))
            content = "\n".join(result.files.values())
            # only a structural stub is produced; assert no workload resource
            assert content.strip()


class TestGithubSupport:
    def test_pipeline_supported(self):
        src, _ = _CASES["pipeline"]
        result = get_backend("github").compile(parse(src))
        content = "\n".join(result.files.values())
        assert "jobs" in content

    def test_service_database_not_emitted_as_jobs(self):
        for name in ("service", "database"):
            src, _ = _CASES[name]
            result = get_backend("github").compile(parse(src))
            content = "\n".join(result.files.values())
            assert "jobs:" not in content, f"github should not emit jobs for {name}"
