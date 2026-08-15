"""Security lint rule tests."""

from __future__ import annotations

import pytest

from infra import parse, validate


def v(source: str):
    return validate(parse(source))


class TestSEC001HardcodedSecret:
    @pytest.mark.parametrize("name,value", [
        ("PASSWORD", "mysecret"), ("DB_PASSWORD", "dbpass123"),
        ("API_KEY", "somevalue"), ("TOKEN", "tok123"), ("SECRET", "mysecret"),
    ])
    def test_triggers_secret_names(self, name, value):
        r = v(f'service api {{ image: "nginx:1.0" env {{ {name}: "{value}" }} }}')
        assert any(e.code == "SEC001" for e in r.errors), f"SEC001 not triggered for {name}"

    def test_no_trigger_nonsecret_env(self):
        r = v('service api { image: "nginx:1.0" env { LOG_LEVEL: "info" PORT: "8080" } }')
        assert not any(e.code == "SEC001" for e in r.errors)

    def test_no_trigger_secret_ref(self):
        r = v('service api { image: "nginx:1.0" env { PASSWORD: from secret "db-creds" } }')
        assert not any(e.code == "SEC001" for e in r.errors)

    def test_hint_present(self):
        r = v('service api { image: "nginx:1.0" env { PASSWORD: "bad" } }')
        e = next(e for e in r.errors if e.code == "SEC001")
        assert e.hint is not None


class TestSEC002SecretPattern:
    @pytest.mark.parametrize("value", [
        "sk-abcdefghijklmnopqrstuvwxyz1234567890",
        "AKIAIOSFODNN7EXAMPLE1234",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
    ])
    def test_triggers_known_patterns(self, value):
        r = v(f'service api {{ image: "nginx:1.0" env {{ API: "{value}" }} }}')
        codes = [e.code for e in r.errors]
        assert "SEC002" in codes or "SEC001" in codes


class TestSEC003MutableTag:
    @pytest.mark.parametrize("tag", ["latest", "master", "main", "dev", "nightly"])
    def test_triggers_mutable_tags(self, tag):
        r = v(f'service a {{ image: "nginx:{tag}" }}')
        assert any(w.code == "SEC003" for w in r.warnings), f"SEC003 not triggered for {tag}"

    def test_no_trigger_pinned(self):
        assert not any(w.code == "SEC003" for w in v('service a { image: "nginx:1.25.3" }').warnings)

    def test_no_trigger_sha(self):
        assert not any(w.code == "SEC003" for w in v('service a { image: "nginx@sha256:abc123" }').warnings)


class TestSEC004Privileged:
    def test_triggers_privileged(self):
        r = v('service a { image: "nginx:1.0" security { privileged: true } }')
        assert any(e.code == "SEC004" for e in r.errors)

    def test_no_trigger_without_security(self):
        assert not any(e.code == "SEC004" for e in v('service a { image: "nginx:1.0" }').errors)

    def test_blocks_is_valid(self):
        r = v('service a { image: "nginx:1.0" security { privileged: true } }')
        assert not r.is_valid


class TestSEC006SSLDisabled:
    def test_triggers_ssl_false(self):
        assert any(w.code == "SEC006" for w in v('database db { type: postgres ssl: false }').warnings)

    def test_no_trigger_ssl_true(self):
        assert not any(w.code == "SEC006" for w in v('database db { type: postgres ssl: true }').warnings)

    def test_no_trigger_default(self):
        assert not any(w.code == "SEC006" for w in v('database db { type: postgres }').warnings)


class TestSEC007HardcodedSecretValue:
    def test_triggers_hardcoded(self):
        r = v('secret db-creds { password: "supersecretpass123" }')
        assert any(e.code == "SEC007" for e in r.errors)

    def test_no_trigger_from_env(self):
        r = v('secret db-creds { password: from env "DB_PASSWORD" }')
        assert not any(e.code == "SEC007" for e in r.errors)

    def test_no_trigger_short_value(self):
        r = v('secret s { key: "short" }')
        assert not any(e.code == "SEC007" for e in r.errors)


class TestSecurityIntegration:
    def test_all_security_findings_have_hints(self):
        src = ('service api { image: "nginx:latest" env { PASSWORD: "bad" } '
               'security { privileged: true } }\n'
               'database db { type: postgres ssl: false }\n'
               'secret s { key: "hardcodedvalue123" }')
        r = v(src)
        sec = [f for f in r.errors + r.warnings if f.code and f.code.startswith("SEC")]
        assert sec
        for f in sec:
            assert f.hint, f"{f.code} missing hint"

    def test_security_errors_block_is_valid(self):
        r = v('service api { image: "nginx:1.0" env { PASSWORD: "hardcoded" } }')
        assert not r.is_valid

    def test_security_warnings_keep_valid(self):
        r = v('service api { image: "nginx:latest" }')
        assert r.is_valid and r.has_warnings
