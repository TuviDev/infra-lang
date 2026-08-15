"""Session 10 - Pod affinity / anti-affinity (Zadanie 1)."""

from __future__ import annotations

import yaml

from infra import parse
from infra.backends import get_backend
from infra.parser import ast_nodes as n


def _service(src: str) -> n.ServiceDef:
    prog = parse(src)
    return next(
        s
        for s in prog.statements
        if getattr(getattr(s, "location", None), "file", "") != "<prelude>"
    )


def _k8s(src: str) -> str:
    return get_backend("kubernetes").compile(parse(src)).files["infra.yaml"]


class TestAffinityParsing:
    def test_parse_prefer_and_avoid(self):
        svc = _service(
            'service api { image: "nginx:1.0" affinity { prefer_same: [frontend] '
            "avoid_same: [api] } }"
        )
        assert isinstance(svc.affinity, n.AffinitySpec)
        assert svc.affinity.prefer_same == ("frontend",)
        assert svc.affinity.avoid_same == ("api",)

    def test_parse_with_colon_variant(self):
        svc = _service(
            'service api { image: "nginx:1.0" affinity: { prefer_same: [db] } }'
        )
        assert svc.affinity.prefer_same == ("db",)
        assert svc.affinity.avoid_same == ()

    def test_no_affinity_defaults_none(self):
        svc = _service('service api { image: "nginx:1.0" }')
        assert svc.affinity is None


class TestAffinityKubernetes:
    def test_pod_affinity_emitted(self):
        content = _k8s(
            'service api { image: "nginx:1.0" affinity { prefer_same: [frontend] '
            "avoid_same: [api] } }"
        )
        assert "podAffinity:" in content
        assert "podAntiAffinity:" in content
        assert "app.kubernetes.io/name: frontend" in content
        assert "app.kubernetes.io/name: api" in content

    def test_no_affinity_section_when_absent(self):
        content = _k8s('service api { image: "nginx:1.0" }')
        assert "affinity:" not in content

    def test_output_is_valid_yaml(self):
        content = _k8s(
            'service api { image: "nginx:1.0" affinity { prefer_same: [a] '
            "avoid_same: [b] } }"
        )
        docs = list(yaml.safe_load_all(content))
        assert len(docs) == 2  # Deployment + Service
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        aff = deployment["spec"]["template"]["spec"]["affinity"]
        assert "podAffinity" in aff
        assert "podAntiAffinity" in aff
