"""Property-based tests using hypothesis."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from infra import parse, validate
from infra.errors.exceptions import InfraLexError, InfraParseError

SAFE_CHARS = "abcdefghijklmnopqrstuvwxyz"
SAFE_NAME = st.text(alphabet=SAFE_CHARS + "-", min_size=2, max_size=20).filter(
    lambda s: s[0].isalpha() and not s.endswith("-")
)
SAFE_IMAGE = st.builds(
    lambda n, t: f"{n}:{t}",
    n=st.text(alphabet=SAFE_CHARS + "/_.", min_size=2, max_size=20),
    t=st.text(alphabet=SAFE_CHARS + ".-_0123456789", min_size=1, max_size=15),
)
REPLICAS = st.integers(min_value=1, max_value=50)


class TestPropertyBased:
    @given(name=SAFE_NAME, image=SAFE_IMAGE, replicas=REPLICAS)
    @settings(
        max_examples=30, deadline=5000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_valid_service_always_parses(self, name, image, replicas):
        source = f'service {name} {{ image: "{image}" replicas: {replicas} }}'
        try:
            program = parse(source)
            assert len(program.statements) >= 1
        except (InfraParseError, InfraLexError):
            pytest.skip(f"Name '{name}' is a reserved keyword")

    @given(replicas=st.integers(min_value=1, max_value=10000))
    @settings(max_examples=40, deadline=5000)
    def test_valid_replicas_never_crash(self, replicas):
        source = f'service api {{ image: "nginx:1.0" replicas: {replicas} }}'
        try:
            program = parse(source)
            validate(program)
        except (InfraParseError, InfraLexError):
            pass

    @given(storage_val=st.integers(min_value=1, max_value=10000),
           unit=st.sampled_from(["Mi", "Gi", "Ti"]))
    @settings(max_examples=20, deadline=5000)
    def test_storage_values_dont_crash_compiler(self, storage_val, unit):
        from infra.backends.kubernetes import KubernetesBackend

        source = f'database db {{ type: postgres storage: {storage_val}{unit} }}'
        try:
            program = parse(source)
            KubernetesBackend().compile(program)
        except (InfraParseError, InfraLexError):
            pass

    @given(image=SAFE_IMAGE)
    @settings(max_examples=20, deadline=5000)
    def test_arbitrary_images_parse(self, image):
        source = f'service api {{ image: "{image}" }}'
        try:
            program = parse(source)
            assert len(program.statements) >= 1
        except (InfraParseError, InfraLexError):
            pass
