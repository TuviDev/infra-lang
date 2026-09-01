"""Autoscale (HPA) and disruption (PDB) tests."""

from __future__ import annotations

import pytest
import yaml

from infra import parse, validate
from infra.backends.kubernetes import KubernetesBackend
from infra.parser.ast_nodes import AutoscaleSpec, DisruptionSpec, ServiceDef


def get_svc(source):
    p = parse(source)
    return next(s for s in p.statements if isinstance(s, ServiceDef))


def k8s_docs(source):
    p = parse(source)
    result = KubernetesBackend().compile(p)
    content = "\n".join(result.files.values())
    return [d for d in yaml.safe_load_all(content) if d is not None]


def kinds(source):
    return [d["kind"] for d in k8s_docs(source)]


class TestAutoscaleParsing:
    def test_parses_min_max_replicas(self):
        svc = get_svc(
            'service api { image: "nginx:1.0" autoscale { min: 2, max: 10 } }'
        )
        assert svc.autoscale is not None
        assert svc.autoscale.min_replicas == 2
        assert svc.autoscale.max_replicas == 10

    def test_default_target_cpu(self):
        svc = get_svc('service api { image: "nginx:1.0" autoscale { min: 1, max: 5 } }')
        assert svc.autoscale.target_cpu == 70

    def test_custom_target_cpu(self):
        svc = get_svc(
            'service api { image: "nginx:1.0" autoscale { min: 2, max: 10, target_cpu: '
            '80 } }'
        )
        assert svc.autoscale.target_cpu == 80

    def test_target_memory(self):
        svc = get_svc(
            'service api { image: "nginx:1.0" autoscale { min: 2, max: 10, '
            'target_memory: 85 } }'
        )
        assert svc.autoscale.target_memory == 85

    def test_scale_delays(self):
        svc = get_svc(
            'service api { image: "nginx:1.0" autoscale { min: 2, max: 10, '
            'scale_up_delay: 60s, scale_down_delay: 5min } }'
        )
        assert svc.autoscale.scale_up_delay is not None
        assert svc.autoscale.scale_down_delay is not None

    def test_no_autoscale_is_none(self):
        assert get_svc('service api { image: "nginx:1.0" }').autoscale is None

    def test_autoscale_is_frozen(self):
        spec = AutoscaleSpec(min_replicas=2, max_replicas=10)
        with pytest.raises((AttributeError, TypeError)):
            spec.min_replicas = 5  # type: ignore


class TestAutoscaleKubernetes:
    def test_generates_hpa(self):
        assert "HorizontalPodAutoscaler" in kinds(
            'service api { image: "nginx:1.0" autoscale { min: 2, max: 10 } }'
        )

    def test_hpa_min(self):
        docs = k8s_docs(
            'service api { image: "nginx:1.0" autoscale { min: 3, max: 15 } }'
        )
        hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler")
        assert hpa["spec"]["minReplicas"] == 3

    def test_hpa_max(self):
        docs = k8s_docs(
            'service api { image: "nginx:1.0" autoscale { min: 2, max: 20 } }'
        )
        hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler")
        assert hpa["spec"]["maxReplicas"] == 20

    def test_hpa_cpu_metric(self):
        docs = k8s_docs(
            'service api { image: "nginx:1.0" autoscale { min: 2, max: 10, target_cpu: '
            '75 } }'
        )
        hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler")
        cpu = [
            m
            for m in hpa["spec"]["metrics"]
            if m.get("resource", {}).get("name") == "cpu"
        ]
        assert len(cpu) == 1
        assert cpu[0]["resource"]["target"]["averageUtilization"] == 75

    def test_hpa_memory_metric_when_set(self):
        docs = k8s_docs(
            'service api { image: "nginx:1.0" autoscale { min: 2, max: 10, '
            'target_memory: 80 } }'
        )
        hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler")
        mem = [
            m
            for m in hpa["spec"]["metrics"]
            if m.get("resource", {}).get("name") == "memory"
        ]
        assert len(mem) == 1

    def test_no_autoscale_no_hpa(self):
        assert "HorizontalPodAutoscaler" not in kinds(
            'service api { image: "nginx:1.0" }'
        )

    def test_hpa_target_ref(self):
        docs = k8s_docs(
            'service myapp { image: "nginx:1.0" autoscale { min: 2, max: 10 } }'
        )
        hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler")
        ref = hpa["spec"]["scaleTargetRef"]
        assert ref["kind"] == "Deployment" and ref["name"] == "myapp"

    def test_hpa_managed_by_label(self):
        docs = k8s_docs(
            'service api { image: "nginx:1.0" autoscale { min: 2, max: 10 } }'
        )
        hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler")
        assert "app.kubernetes.io/managed-by" in hpa["metadata"]["labels"]

    def test_hpa_valid_yaml(self):
        p = parse('service api { image: "nginx:1.0" autoscale { min: 2, max: 20 } }')
        result = KubernetesBackend().compile(p)
        for content in result.files.values():
            for doc in yaml.safe_load_all(content):
                assert doc is None or isinstance(doc, dict)


class TestAutoscaleReliability:
    def test_rel011_no_limits_with_autoscale(self):
        r = validate(
            parse('service api { image: "nginx:1.0" autoscale { min: 2, max: 10 } }')
        )
        assert any(w.code == "REL011" for w in r.warnings)

    def test_rel011_not_triggered_with_limits(self):
        r = validate(
            parse(
                'service api { image: "nginx:1.0" autoscale { min: 2, max: 10 } '
                'resources { limits { cpu: 500m } } }'
            )
        )
        assert not any(w.code == "REL011" for w in r.warnings)

    def test_rel011_not_triggered_without_autoscale(self):
        r = validate(parse('service api { image: "nginx:1.0" }'))
        assert not any(w.code == "REL011" for w in r.warnings)

    def test_rel011_has_hint(self):
        r = validate(
            parse('service api { image: "nginx:1.0" autoscale { min: 2, max: 10 } }')
        )
        w = next((w for w in r.warnings if w.code == "REL011"), None)
        if w:
            assert w.hint is not None


class TestDisruptionParsing:
    def test_min_available(self):
        svc = get_svc(
            'service api { image: "nginx:1.0" disruption { min_available: 1 } }'
        )
        assert svc.disruption is not None
        assert svc.disruption.min_available == 1

    def test_max_unavailable(self):
        svc = get_svc(
            'service api { image: "nginx:1.0" disruption { max_unavailable: 1 } }'
        )
        assert svc.disruption is not None
        assert svc.disruption.max_unavailable == 1

    def test_no_disruption_is_none(self):
        assert get_svc('service api { image: "nginx:1.0" }').disruption is None

    def test_disruption_is_frozen(self):
        spec = DisruptionSpec(min_available=1)
        with pytest.raises((AttributeError, TypeError)):
            spec.min_available = 2  # type: ignore


class TestDisruptionKubernetes:
    def test_generates_pdb(self):
        assert "PodDisruptionBudget" in kinds(
            'service api { image: "nginx:1.0" disruption { min_available: 1 } }'
        )

    def test_pdb_min_available(self):
        docs = k8s_docs(
            'service api { image: "nginx:1.0" disruption { min_available: 2 } }'
        )
        pdb = next(d for d in docs if d["kind"] == "PodDisruptionBudget")
        assert pdb["spec"]["minAvailable"] == 2

    def test_pdb_max_unavailable(self):
        docs = k8s_docs(
            'service api { image: "nginx:1.0" disruption { max_unavailable: 1 } }'
        )
        pdb = next(d for d in docs if d["kind"] == "PodDisruptionBudget")
        assert pdb["spec"]["maxUnavailable"] == 1

    def test_no_disruption_no_pdb(self):
        assert "PodDisruptionBudget" not in kinds('service api { image: "nginx:1.0" }')

    def test_pdb_selector_matches_service(self):
        docs = k8s_docs(
            'service myapp { image: "nginx:1.0" disruption { min_available: 1 } }'
        )
        pdb = next(d for d in docs if d["kind"] == "PodDisruptionBudget")
        labels = pdb["spec"]["selector"]["matchLabels"]
        assert "app" in labels and labels["app"] == "myapp"

    def test_pdb_valid_yaml(self):
        p = parse('service api { image: "nginx:1.0" disruption { min_available: 1 } }')
        result = KubernetesBackend().compile(p)
        for content in result.files.values():
            for doc in yaml.safe_load_all(content):
                assert doc is None or isinstance(doc, dict)
