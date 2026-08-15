"""Schedule-block tests: parsing, Kubernetes output and validation."""

from __future__ import annotations

import yaml

from infra import parse, validate
from infra.backends.kubernetes import KubernetesBackend


def compile_k8s(source: str) -> dict:
    return KubernetesBackend().compile(parse(source)).files


def all_k8s_docs(source: str) -> list:
    content = "\n".join(compile_k8s(source).values())
    return [d for d in yaml.safe_load_all(content) if d is not None]


def _svc(source: str):
    program = parse(source)
    return next(s for s in program.statements if hasattr(s, "schedule"))


class TestScheduleParsing:
    def test_default_only(self):
        svc = _svc('service api { image: "nginx:1.0" schedule { default: replicas 2 } }')
        assert svc.schedule.default is not None
        assert svc.schedule.default.replicas == 2

    def test_with_cron_slot(self):
        svc = _svc('service api { image: "nginx:1.0" schedule { default: replicas 2 "0 9 * * 1-5": replicas 5 } }')
        assert len(svc.schedule.slots) >= 1
        assert svc.schedule.slots[0].cron == "0 9 * * 1-5"
        assert svc.schedule.slots[0].config.replicas == 5

    def test_multiple_slots(self):
        svc = _svc('service api { image: "nginx:1.0" schedule { default: replicas 1 "0 9 * * 1-5": replicas 5 "0 22 * * *": replicas 1 "0 0 * * 6,7": replicas 1 } }')
        assert len(svc.schedule.slots) == 3

    def test_config_object(self):
        svc = _svc('service api { image: "nginx:1.0" schedule { "0 9 * * 1-5": { replicas: 5, cpu: 500m, memory: 512Mi } } }')
        slot = svc.schedule.slots[0]
        assert slot.config.replicas == 5
        assert slot.config.cpu is not None

    def test_without_schedule_is_none(self):
        program = parse('service api { image: "nginx:1.0" }')
        svc = next(s for s in program.statements if hasattr(s, "schedule"))
        assert svc.schedule is None


class TestScheduleKubernetes:
    def test_generates_cronjob(self):
        docs = all_k8s_docs('service api { image: "nginx:1.0" schedule { default: replicas 2 "0 9 * * 1-5": replicas 5 } }')
        assert "CronJob" in [d["kind"] for d in docs]

    def test_cronjob_correct_schedule(self):
        docs = all_k8s_docs('service api { image: "nginx:1.0" schedule { "0 9 * * 1-5": replicas 5 } }')
        cj = next(d for d in docs if d["kind"] == "CronJob")
        assert cj["spec"]["schedule"] == "0 9 * * 1-5"

    def test_cronjob_scales_correct_replicas(self):
        docs = all_k8s_docs('service api { image: "nginx:1.0" schedule { "0 9 * * 1-5": replicas 10 } }')
        cj = next(d for d in docs if d["kind"] == "CronJob")
        containers = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        assert "--replicas=10" in containers[0]["command"]

    def test_generates_hpa(self):
        docs = all_k8s_docs('service api { image: "nginx:1.0" schedule { default: replicas 2 "0 9 * * 1-5": replicas 10 } }')
        assert "HorizontalPodAutoscaler" in [d["kind"] for d in docs]

    def test_hpa_min_from_default(self):
        docs = all_k8s_docs('service api { image: "nginx:1.0" schedule { default: replicas 2 "0 9 * * 1-5": replicas 8 } }')
        hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler")
        assert hpa["spec"]["minReplicas"] == 2

    def test_hpa_max_from_peak(self):
        docs = all_k8s_docs('service api { image: "nginx:1.0" schedule { default: replicas 2 "0 9 * * 1-5": replicas 8 "0 20 * * *": replicas 12 } }')
        hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler")
        assert hpa["spec"]["maxReplicas"] >= 12

    def test_generates_rbac(self):
        docs = all_k8s_docs('service api { image: "nginx:1.0" schedule { "0 9 * * 1-5": replicas 5 } }')
        kinds = [d["kind"] for d in docs]
        assert "ServiceAccount" in kinds
        assert "ClusterRole" in kinds
        assert "ClusterRoleBinding" in kinds

    def test_rbac_correct_name(self):
        docs = all_k8s_docs('service api { image: "nginx:1.0" schedule { "0 9 * * 1-5": replicas 5 } }')
        sa = next(d for d in docs if d["kind"] == "ServiceAccount")
        assert sa["metadata"]["name"] == "infra-scheduler"

    def test_multiple_services_one_rbac(self):
        src = ('service api { image: "nginx:1.0" schedule { "0 9 * * 1-5": replicas 5 } }\n'
               'service worker { image: "redis:7" schedule { "0 9 * * 1-5": replicas 3 } }')
        docs = all_k8s_docs(src)
        sas = [d for d in docs if d["kind"] == "ServiceAccount" and d["metadata"]["name"] == "infra-scheduler"]
        assert len(sas) == 1

    def test_no_schedule_no_cronjobs(self):
        docs = all_k8s_docs('service api { image: "nginx:1.0" }')
        kinds = [d["kind"] for d in docs]
        assert "CronJob" not in kinds
        assert "HorizontalPodAutoscaler" not in kinds

    def test_output_valid_yaml(self):
        src = 'service api { image: "nginx:1.0" schedule { default: replicas 2 "0 9 * * 1-5": replicas 5 "0 22 * * *": replicas 1 } }'
        for content in compile_k8s(src).values():
            docs = list(yaml.safe_load_all(content))
            assert all(d is None or isinstance(d, dict) for d in docs)


class TestScheduleValidation:
    def test_valid_cron_no_error(self):
        r = validate(parse('service api { image: "nginx:1.0" schedule { "0 9 * * 1-5": replicas 3 } }'))
        assert not any(e.code == "E010" for e in r.errors)

    def test_invalid_cron_error(self):
        r = validate(parse('service api { image: "nginx:1.0" schedule { "0 9 * *": replicas 3 } }'))
        assert not r.is_valid
        assert any(e.code == "E010" for e in r.errors)

    def test_zero_replicas_error(self):
        r = validate(parse('service api { image: "nginx:1.0" schedule { "0 9 * * 1-5": replicas 0 } }'))
        assert not r.is_valid
