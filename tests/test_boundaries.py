"""Boundary value tests for numeric fields."""

from __future__ import annotations

import pytest

from infra import parse, validate
from infra.errors.exceptions import InfraParseError


def v(source: str):
    return validate(parse(source))


class TestReplicas:
    def test_zero_error(self):
        assert not v('service a { image:"nginx:1.0" replicas:0 }').is_valid

    def test_one_valid(self):
        assert v('service a { image:"nginx:1.0" replicas:1 }').is_valid

    def test_1000_valid(self):
        assert v('service a { image:"nginx:1.0" replicas:1000 }').is_valid

    def test_negative(self):
        try:
            assert not v('service a { image:"nginx:1.0" replicas:-1 }').is_valid
        except InfraParseError:
            pass


class TestPorts:
    def test_zero_error(self):
        assert not v('service a { image:"nginx:1.0" port:0 }').is_valid

    def test_one_valid(self):
        assert v('service a { image:"nginx:1.0" port:1 }').is_valid

    def test_65535_valid(self):
        assert v('service a { image:"nginx:1.0" port:65535 }').is_valid

    def test_65536_error(self):
        assert not v('service a { image:"nginx:1.0" port:65536 }').is_valid


class TestStorage:
    def test_small_valid(self):
        assert v('database db { type:postgres storage:1Mi }').is_valid

    def test_large_valid(self):
        assert v('database db { type:postgres storage:100Ti }').is_valid


class TestDatabase:
    def test_db_replicas_zero_error(self):
        assert not v('database db { type:postgres replicas:0 }').is_valid


class TestNames:
    def test_duplicate_names_error(self):
        r = v('service api { image: "nginx:1.0" }\nservice api { image: "redis:7" }')
        assert not r.is_valid
        assert any(e.code == "E002" for e in r.errors)
