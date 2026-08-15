"""Extends resolver tests (environment + service inheritance)."""

from __future__ import annotations

import pytest

from infra import parse, validate
from infra.parser.ast_nodes import EnvironmentDef, ServiceDef
from infra.resolver.extends import ExtendsCycleError, ExtendsResolver


def resolved(src: str):
    return ExtendsResolver().resolve(parse(src))


def env_named(program, name):
    return next(e for e in program.statements if isinstance(e, EnvironmentDef) and e.name == name)


def svc_named(program, name):
    return next(s for s in program.statements if isinstance(s, ServiceDef) and s.name == name)


class TestEnvironmentExtends:
    def test_inherits_namespace_and_provider(self):
        program = resolved(
            'environment base { namespace: "myapp" provider: aws }\n'
            'environment prod extends base { namespace: "prod-ns" }'
        )
        prod = env_named(program, "prod")
        assert prod.namespace == "prod-ns"          # child wins
        assert prod.provider == "aws"                # inherited from parent
        assert prod.extends is None                  # resolved away

    def test_child_does_not_override(self):
        program = resolved(
            'environment base { region: "eu-west-1" }\n'
            'environment child extends base { namespace: "child-ns" }'
        )
        child = env_named(program, "child")
        assert child.region == "eu-west-1"
        assert child.namespace == "child-ns"

    def test_labels_merged_by_key(self):
        program = resolved(
            'environment base { labels: { tier: "app", env: "base" } }\n'
            'environment child extends base { labels: { env: "prod", region: "eu" } }'
        )
        child = env_named(program, "child")
        labels = dict(child.labels)
        assert labels["tier"] == "app"    # inherited
        assert labels["env"] == "prod"    # child overrides
        assert labels["region"] == "eu"   # child adds

    def test_multilevel_inheritance(self):
        program = resolved(
            'environment root { provider: aws }\n'
            'environment mid extends root { region: "eu" }\n'
            'environment leaf extends mid { namespace: "leaf-ns" }'
        )
        leaf = env_named(program, "leaf")
        assert leaf.provider == "aws"
        assert leaf.region == "eu"
        assert leaf.namespace == "leaf-ns"

    def test_circular_extends_raises(self):
        src = 'environment a extends b { namespace: "a" }\nenvironment b extends a { namespace: "b" }'
        with pytest.raises((ExtendsCycleError, Exception)):
            ExtendsResolver().resolve(parse(src))

    def test_extends_unknown_raises(self):
        src = 'environment prod extends nonexistent { namespace: "prod" }'
        with pytest.raises(Exception):
            ExtendsResolver().resolve(parse(src))

    def test_no_extends_unchanged(self):
        program = resolved('environment prod { namespace: "prod-ns" }')
        env = env_named(program, "prod")
        assert env.namespace == "prod-ns"
        assert env.extends is None


class TestServiceExtends:
    def test_inherits_image_and_replicas(self):
        program = resolved(
            'service base { image: "img:1" replicas: 3 }\n'
            'service api extends base { replicas: 5 }'
        )
        api = svc_named(program, "api")
        assert api.image == "img:1"       # inherited
        assert api.replicas == 5          # child wins
        assert api.extends is None

    def test_inherits_env_and_resources(self):
        program = resolved(
            'service base { env { LOG: "info" } resources { requests { cpu: 100m } } }\n'
            'service api extends base { replicas: 2 }'
        )
        api = svc_named(program, "api")
        assert any(e.name == "LOG" for e in api.env)
        assert api.resources is not None
        assert api.replicas == 2

    def test_unknown_parent_raises(self):
        src = 'service api extends nosvc { image: "x" }'
        with pytest.raises(Exception):
            ExtendsResolver().resolve(parse(src))

    def test_circular_service_raises(self):
        src = 'service a extends b { image: "x" }\nservice b extends a { image: "y" }'
        with pytest.raises((ExtendsCycleError, Exception)):
            ExtendsResolver().resolve(parse(src))


class TestValidationIntegration:
    def test_validate_with_extends(self):
        src = (
            'environment base { namespace: "myapp" }\n'
            'environment prod extends base { namespace: "prod" }'
        )
        result = validate(src)
        assert result.is_valid
        assert not result.errors

    def test_validate_extends_unknown_raises(self):
        src = 'environment prod extends nope { namespace: "prod" }'
        with pytest.raises(Exception):
            validate(src)
