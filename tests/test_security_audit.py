"""Deep audit of security rules: trigger, no-trigger, message, hint, severity."""

from __future__ import annotations

import pytest

from infra import parse, validate


def v(source):
    return validate(parse(source))


def get_finding(source, code):
    r = v(source)
    all_f = r.errors + r.warnings
    return next((f for f in all_f if f.code == code), None)


class TestSEC001_HardcodedSecret:
    TRIGGER_NAMES = [
        "PASSWORD", "PASSWD", "DB_PASSWORD",
        "API_KEY", "TOKEN", "SECRET",
        "ACCESS_KEY", "CLIENT_SECRET",
    ]

    @pytest.mark.parametrize("name", TRIGGER_NAMES)
    def test_triggers_for_secret_name(self, name):
        source = f'service s {{ image: "nginx:1.25" env {{ {name}: "somevalue" }} }}'
        assert get_finding(source, "SEC001") is not None, f"SEC001 should trigger for {name}"

    def test_not_trigger_for_safe_name(self):
        for name in ["LOG_LEVEL", "PORT", "APP_NAME", "DEBUG", "WORKERS", "TIMEOUT"]:
            source = f'service s {{ image: "nginx:1.25" env {{ {name}: "value" }} }}'
            assert get_finding(source, "SEC001") is None, f"SEC001 should NOT trigger for {name}"

    def test_not_trigger_for_secret_ref(self):
        source = 'service s { image: "nginx:1.25" env { PASSWORD: from secret "creds".key } }'
        assert get_finding(source, "SEC001") is None

    def test_message_contains_variable_name(self):
        f = get_finding('service s { image: "nginx:1.25" env { API_KEY: "val" } }', "SEC001")
        assert f is not None
        assert "API_KEY" in f.message

    def test_hint_suggests_from_secret(self):
        f = get_finding('service s { image: "nginx:1.25" env { PASSWORD: "bad" } }', "SEC001")
        assert f is not None
        assert f.hint is not None
        assert "secret" in f.hint.lower()

    def test_severity_is_error(self):
        r = v('service s { image: "nginx:1.25" env { PASSWORD: "bad" } }')
        assert any(e.code == "SEC001" for e in r.errors)
        assert not r.is_valid


class TestSEC003_MutableTag:
    MUTABLE_TAGS = ["latest", "master", "main", "dev", "nightly", "edge"]

    @pytest.mark.parametrize("tag", MUTABLE_TAGS)
    def test_triggers_for_mutable_tag(self, tag):
        assert get_finding(f'service s {{ image: "nginx:{tag}" }}', "SEC003") is not None

    def test_not_trigger_for_pinned_version(self):
        for tag in ["1.25.3", "v1.0.0", "1.25.3-alpine", "20231201", "sha256abc"]:
            assert get_finding(f'service s {{ image: "nginx:{tag}" }}', "SEC003") is None

    def test_not_trigger_for_sha_digest(self):
        assert get_finding('service s { image: "nginx@sha256:abc123" }', "SEC003") is None

    def test_severity_is_warning_not_error(self):
        r = v('service s { image: "nginx:latest" }')
        assert r.is_valid
        assert any(w.code == "SEC003" for w in r.warnings)


class TestSEC004_PrivilegedContainer:
    def test_triggers_for_privileged_true(self):
        assert get_finding('service s { image: "nginx:1.25" security { privileged: true } }', "SEC004") is not None

    def test_not_trigger_without_security_block(self):
        assert get_finding('service s { image: "nginx:1.25" }', "SEC004") is None

    def test_not_trigger_with_privileged_false(self):
        assert get_finding('service s { image: "nginx:1.25" security { privileged: false user: 1000 } }', "SEC004") is None

    def test_severity_is_error(self):
        r = v('service s { image: "nginx:1.25" security { privileged: true } }')
        assert not r.is_valid
        assert any(e.code == "SEC004" for e in r.errors)


class TestSEC006_SSLDisabled:
    def test_triggers_for_ssl_false(self):
        assert get_finding("database db { type: postgres ssl: false }", "SEC006") is not None

    def test_not_trigger_for_ssl_true(self):
        assert get_finding("database db { type: postgres ssl: true }", "SEC006") is None

    def test_not_trigger_for_ssl_default(self):
        assert get_finding("database db { type: postgres }", "SEC006") is None

    def test_severity_is_warning(self):
        r = v("database db { type: postgres ssl: false }")
        assert r.is_valid
        assert any(w.code == "SEC006" for w in r.warnings)


class TestAllSecurityFindingsHaveHints:
    def test_every_sec_finding_has_hint(self):
        sources = [
            'service s { image: "nginx:latest" env { PASSWORD: "bad" } }',
            'service s { image: "nginx:1.25" security { privileged: true } }',
            "database db { type: postgres ssl: false }",
            'secret s { key: "hardcodedval123456" }',
            'service s { image: "nginx:1.25" ingress { host: "x.com" } }',
            'service s { image: "nginx:1.25" }',
        ]
        for source in sources:
            r = v(source)
            sec = [f for f in r.errors + r.warnings if f.code and f.code.startswith("SEC")]
            for finding in sec:
                assert finding.hint is not None, f"{finding.code} missing hint: {finding.message}"
                assert len(finding.hint) > 10, f"{finding.code} hint too short"
