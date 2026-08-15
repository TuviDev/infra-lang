"""Template-string interpolation tests."""

from __future__ import annotations

import yaml

from infra import parse
from infra.backends.compose import DockerComposeBackend
from infra.backends.kubernetes import KubernetesBackend


def compile_k8s(source: str) -> dict:
    program = parse(source)
    return KubernetesBackend().compile(program).files


def compile_compose(source: str) -> dict:
    program = parse(source)
    return DockerComposeBackend().compile(program).files


class TestTemplateInterpolation:
    def test_const_in_image(self):
        files = compile_k8s('const VERSION = "1.2.3"\nservice api { image: `myapp:{VERSION}` }')
        content = "\n".join(files.values())
        assert "myapp:1.2.3" in content
        assert "{VERSION}" not in content

    def test_multiple_vars_in_template(self):
        files = compile_k8s(
            'const ORG = "myorg"\nconst REPO = "myapp"\nconst TAG = "v2.0.0"\n'
            'service api { image: `{ORG}/{REPO}:{TAG}` }'
        )
        content = "\n".join(files.values())
        assert "myorg/myapp:v2.0.0" in content

    def test_let_variable_used(self):
        files = compile_k8s(
            'let app = "nginx"\nlet tag = "stable"\nservice api { image: `{app}:{tag}` }'
        )
        content = "\n".join(files.values())
        assert "nginx:stable" in content

    def test_undefined_var_becomes_placeholder(self):
        files = compile_k8s('service api { image: `myapp:{UNDEFINED}` }')
        content = "\n".join(files.values())
        assert "myapp:" in content
        assert "${UNDEFINED}" in content

    def test_plain_string_unchanged(self):
        files = compile_k8s('service api { image: "nginx:1.25" }')
        content = "\n".join(files.values())
        assert "nginx:1.25" in content

    def test_template_in_compose(self):
        files = compile_compose('const TAG = "v3"\nservice api { image: `nginx:{TAG}` }')
        content = "\n".join(files.values())
        assert "nginx:v3" in content

    def test_template_with_concat_expressions(self):
        files = compile_k8s(
            'const MAJOR = "1"\nconst MINOR = "2"\n'
            'service api { image: `app:{MAJOR}.{MINOR}` }'
        )
        content = "\n".join(files.values())
        assert "app:1.2" in content

    def test_output_is_valid_yaml(self):
        files = compile_k8s('const VERSION = "1.0"\nservice api { image: `nginx:{VERSION}` }')
        for content in files.values():
            for doc in yaml.safe_load_all(content):
                assert doc is None or isinstance(doc, dict)
