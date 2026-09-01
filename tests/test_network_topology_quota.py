"""Tests for per-service network policies, topology spread, and env quotas."""

from __future__ import annotations

import pytest
import yaml

from infra import parse, validate
from infra.backends.kubernetes import KubernetesBackend
from infra.parser.ast_nodes import (
    EnvironmentDef,
    NetworkPolicySpec,
    ServiceDef,
    TopologySpec,
)


def get_svc(source):
    p = parse(source)
    return next(s for s in p.statements if isinstance(s, ServiceDef))


def k8s_docs(source):
    result = KubernetesBackend().compile(parse(source))
    content = "\n".join(result.files.values())
    return [d for d in yaml.safe_load_all(content) if d is not None]


def kinds(source):
    return [d["kind"] for d in k8s_docs(source)]


class TestNetworkPolicyParsing:
    def test_parses_allow_from_refs(self):
        svc = get_svc(
            'service api { image: "x" network_policy { allow_from: [frontend, '
            'monitoring] } }'
        )
        assert svc.network_policy is not None
        assert svc.network_policy.allow_from == ("frontend", "monitoring")

    def test_deny_and_egress(self):
        svc = get_svc(
            'service api { image: "x" network_policy { deny_from: ["*"], allow_egress: '
            '[db, cache] } }'
        )
        assert svc.network_policy.deny_from == ("*",)
        assert svc.network_policy.allow_egress == ("db", "cache")

    def test_no_network_policy_is_none(self):
        assert get_svc('service api { image: "x" }').network_policy is None

    def test_frozen(self):
        spec = NetworkPolicySpec(allow_from=("a",))
        with pytest.raises((AttributeError, TypeError)):
            spec.allow_from = ("b",)  # type: ignore


class TestNetworkPolicyKubernetes:
    def test_generates_network_policy(self):
        assert "NetworkPolicy" in kinds(
            'service api { image: "x" network_policy { allow_from: [frontend] } }'
        )

    def test_pod_selector(self):
        docs = k8s_docs(
            'service api { image: "x" network_policy { allow_from: [frontend] } }'
        )
        np_ = next(d for d in docs if d["kind"] == "NetworkPolicy")
        assert (
            np_["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/name"] == "api"
        )

    def test_ingress_from(self):
        docs = k8s_docs(
            'service api { image: "x" network_policy { allow_from: [frontend] } }'
        )
        np_ = next(d for d in docs if d["kind"] == "NetworkPolicy")
        ingress = np_["spec"]["ingress"]
        assert (
            ingress[0]["from"][0]["podSelector"]["matchLabels"][
                "app.kubernetes.io/name"
            ]
            == "frontend"
        )

    def test_egress_to(self):
        docs = k8s_docs(
            'service api { image: "x" network_policy { allow_egress: [db, cache] } }'
        )
        np_ = next(d for d in docs if d["kind"] == "NetworkPolicy")
        egress = np_["spec"]["egress"]
        names = [
            e["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
            for e in egress[0]["to"]
        ]
        assert "db" in names and "cache" in names

    def test_wildcard_deny_no_ingress(self):
        docs = k8s_docs(
            'service api { image: "x" network_policy { deny_from: ["*"] } }'
        )
        np_ = next(d for d in docs if d["kind"] == "NetworkPolicy")
        assert "ingress" not in np_["spec"]

    def test_no_network_policy_no_resource(self):
        assert "NetworkPolicy" not in kinds('service api { image: "x" }')

    def test_managed_by_label(self):
        docs = k8s_docs(
            'service api { image: "x" network_policy { allow_from: [frontend] } }'
        )
        np_ = next(d for d in docs if d["kind"] == "NetworkPolicy")
        assert "app.kubernetes.io/managed-by" in np_["metadata"]["labels"]


class TestNetworkPolicyValidation:
    def test_known_service_no_warning(self):
        r = validate(
            parse(
                'service frontend { image: "x" }\nservice api { image: "y" '
                'network_policy { allow_from: [frontend] } }'
            )
        )
        assert not any(w.code == "W001" for w in r.warnings)

    def test_unknown_service_warning(self):
        r = validate(
            parse('service api { image: "y" network_policy { allow_from: [nope] } }')
        )
        assert any(w.code == "W001" for w in r.warnings)


class TestTopologyParsing:
    def test_parses_spread_by_and_max_skew(self):
        svc = get_svc(
            'service api { image: "x" topology { spread_by: zone, max_skew: 1 } }'
        )
        assert svc.topology is not None
        assert svc.topology.spread_by == "zone"
        assert svc.topology.max_skew == 1

    def test_default_max_skew(self):
        svc = get_svc('service api { image: "x" topology { spread_by: host } }')
        assert svc.topology.max_skew == 1
        assert svc.topology.spread_by == "host"

    def test_no_topology_is_none(self):
        assert get_svc('service api { image: "x" }').topology is None

    def test_frozen(self):
        spec = TopologySpec(spread_by="zone", max_skew=1)
        with pytest.raises((AttributeError, TypeError)):
            spec.max_skew = 2  # type: ignore


class TestTopologyKubernetes:
    def test_zone_topology_key(self):
        docs = k8s_docs(
            'service api { image: "x" topology { spread_by: zone, max_skew: 1 } }'
        )
        dep = next(d for d in docs if d["kind"] == "Deployment")
        constraints = dep["spec"]["template"]["spec"]["topologySpreadConstraints"]
        assert constraints[0]["topologyKey"] == "topology.kubernetes.io/zone"

    def test_host_topology_key(self):
        docs = k8s_docs('service api { image: "x" topology { spread_by: host } }')
        dep = next(d for d in docs if d["kind"] == "Deployment")
        constraints = dep["spec"]["template"]["spec"]["topologySpreadConstraints"]
        assert constraints[0]["topologyKey"] == "kubernetes.io/hostname"

    def test_max_skew(self):
        docs = k8s_docs(
            'service api { image: "x" topology { spread_by: zone, max_skew: 2 } }'
        )
        dep = next(d for d in docs if d["kind"] == "Deployment")
        constraints = dep["spec"]["template"]["spec"]["topologySpreadConstraints"]
        assert constraints[0]["maxSkew"] == 2

    def test_no_topology_no_constraints(self):
        docs = k8s_docs('service api { image: "x" }')
        dep = next(d for d in docs if d["kind"] == "Deployment")
        assert "topologySpreadConstraints" not in dep["spec"]["template"]["spec"]


class TestQuotaParsing:
    def test_parses_quota_max_pods(self):
        p = parse(
            'environment prod { namespace: "ns", quotas { max_cpu: 10cores, '
            'max_memory: '
            '20Gi, max_pods: 100 } }'
        )
        env = next(e for e in p.statements if isinstance(e, EnvironmentDef))
        assert env.quotas is not None
        assert env.quotas.max_pods == 100

    def test_no_quotas_is_none(self):
        p = parse('environment dev { namespace: "d" }')
        env = next(e for e in p.statements if isinstance(e, EnvironmentDef))
        assert env.quotas is None


class TestQuotaKubernetes:
    def test_generates_resource_quota(self):
        assert "ResourceQuota" in kinds(
            'environment prod { namespace: "ns", quotas { max_pods: 100 } }'
        )

    def test_quota_hard_values(self):
        docs = k8s_docs(
            'environment prod { namespace: "ns", quotas { max_cpu: 10cores, '
            'max_memory: '
            '20Gi, max_pods: 100 } }'
        )
        rq = next(d for d in docs if d["kind"] == "ResourceQuota")
        hard = rq["spec"]["hard"]
        assert hard["pods"] == "100"
        assert "limits.cpu" in hard and "limits.memory" in hard

    def test_quota_in_namespace(self):
        docs = k8s_docs(
            'environment prod { namespace: "my-ns", quotas { max_pods: 10 } }'
        )
        rq = next(d for d in docs if d["kind"] == "ResourceQuota")
        assert rq["metadata"]["namespace"] == "my-ns"

    def test_no_quotas_no_resource(self):
        assert "ResourceQuota" not in kinds('environment dev { namespace: "d" }')

    def test_valid_yaml(self):
        result = KubernetesBackend().compile(
            parse('environment prod { namespace: "ns", quotas { max_pods: 50 } }')
        )
        for content in result.files.values():
            for doc in yaml.safe_load_all(content):
                assert doc is None or isinstance(doc, dict)
