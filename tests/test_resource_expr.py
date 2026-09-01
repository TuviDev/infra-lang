"""Resource-expression tests: constants usable inside resources blocks."""

from __future__ import annotations

from infra import parse
from infra.backends.kubernetes import KubernetesBackend


def compile_k8s(source: str) -> str:
    program = parse(source)
    files = KubernetesBackend().compile(program).files
    return "\n".join(files.values())


class TestResourceExpressions:
    def test_const_in_cpu(self):
        content = compile_k8s(
            "const APP_CPU = 500m\n"
            'service api { image: "nginx" resources { cpu: APP_CPU } }'
        )
        assert "500m" in content

    def test_const_in_memory(self):
        content = compile_k8s(
            "const APP_MEM = 256Mi\n"
            'service api { image: "nginx" resources { memory: APP_MEM } }'
        )
        assert "256Mi" in content

    def test_prelude_small_cpu(self):
        content = compile_k8s(
            'service api { image: "nginx" resources { cpu: SMALL_CPU } }'
        )
        assert "100m" in content

    def test_prelude_medium_mem(self):
        content = compile_k8s(
            'service api { image: "nginx" resources { memory: MEDIUM_MEM } }'
        )
        assert "512Mi" in content

    def test_literal_resource_still_works(self):
        content = compile_k8s(
            'service api { image: "nginx" resources { cpu: 250m memory: 128Mi } }'
        )
        assert "250m" in content
        assert "128Mi" in content

    def test_resource_in_limits(self):
        content = compile_k8s(
            "const LIMIT_CPU = 1000m\nconst LIMIT_MEM = 512Mi\n"
            'service api { image: "nginx" '
            "resources { limits { cpu: LIMIT_CPU, memory: LIMIT_MEM } } }"
        )
        assert "1000m" in content
        assert "512Mi" in content
