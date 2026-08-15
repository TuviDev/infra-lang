"""Edge-case audit: the parser and backends must never crash on hostile input.

For every input, the outcome is either a successful parse or an
InfraParseError / InfraLexError. Never AttributeError, KeyError, TypeError,
RecursionError, MemoryError, or a traceback from a third-party library.
"""

from __future__ import annotations

import pytest

from infra import parse
from infra.backends.compose import DockerComposeBackend
from infra.backends.kubernetes import KubernetesBackend
from infra.errors.exceptions import InfraLexError, InfraParseError

SAFE_EXCEPTIONS = (InfraParseError, InfraLexError)


def assert_safe(source):
    try:
        parse(source)
    except SAFE_EXCEPTIONS:
        pass
    except Exception as e:
        pytest.fail(
            f"Unexpected exception {type(e).__name__}: {e}\nSource: {source[:100]}"
        )


class TestParserNeverCrashes:
    TRICKY_INPUTS = [
        "",
        "   \n\n\n   ",
        "#" * 1000,
        "service " * 100,
        "{" * 50 + "}" * 50,
        "let x = " + "1 + " * 100 + "1",
        'service s { image: "' + "a" * 1000 + '" }',
        "service s { " + "image: " * 100 + '"x" }',
        "\x00\x01\x02\x03",
        "🚀💥🔥" * 100,
        "null null null null null",
        "true false true false",
        "@" * 50 + "decorator",
        "import " * 50,
        "from " * 50 + "import",
    ]

    @pytest.mark.parametrize("source", TRICKY_INPUTS)
    def test_safe_for_tricky_input(self, source):
        assert_safe(source)

    def test_deeply_nested_expressions(self):
        assert_safe("let x = " + "(" * 20 + "1" + ")" * 20)

    def test_very_many_services(self):
        source = "\n".join(
            f'service svc{i} {{ image: "img{i}:1.0" }}' for i in range(200)
        )
        assert_safe(source)

    def test_very_many_env_vars(self):
        vars_block = "\n".join(f'  VAR{i}: "value{i}"' for i in range(100))
        source = f"""
        service s {{
            image: "nginx:1.25"
            env {{
{vars_block}
            }}
        }}
        """
        assert_safe(source)

    def test_unicode_in_all_string_positions(self):
        sources = [
            'service s { image: "nginx:日本語" }',
            'let x = "こんにちは"',
            'service s { labels: { key: "héllo wörld" } }',
        ]
        for source in sources:
            assert_safe(source)

    def test_max_depth_recursion_safe(self):
        assert_safe("let x = " + "[" * 100 + "1" + "]" * 100)


VALID_MINIMAL_SOURCES = [
    'service api { image: "nginx:1.25" }',
    "database db { type: postgres }",
    "cache c { type: redis }",
    "queue q { type: rabbitmq }",
    'secret s { key: from env "K" }',
    'config c { VAL: "x" }',
    "network n { }",
    'pipeline p { trigger { branches: ["main"] } stages {} }',
    'environment e { namespace: "ns" }',
    "cluster c { provider: aws }",
]


class TestCompileNeverCrashes:
    @pytest.mark.parametrize("source", VALID_MINIMAL_SOURCES)
    def test_k8s_compile_safe(self, source):
        try:
            program = parse(source)
            result = KubernetesBackend().compile(program)
            assert result is not None
        except Exception as e:
            pytest.fail(f"K8s compile crashed for: {source}\n{type(e).__name__}: {e}")

    @pytest.mark.parametrize("source", VALID_MINIMAL_SOURCES)
    def test_compose_compile_safe(self, source):
        try:
            program = parse(source)
            result = DockerComposeBackend().compile(program)
            assert result is not None
        except Exception as e:
            pytest.fail(f"Compose compile crashed for: {source}\n{type(e).__name__}: {e}")
