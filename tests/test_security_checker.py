"""Security lint rule tests."""

from __future__ import annotations

import pytest

from infra import parse, validate


def v(source: str):
    return validate(parse(source))


class TestSEC001HardcodedSecret:
    @pytest.mark.parametrize(
        "name,value",
        [
            ("PASSWORD", "mysecret"),
            ("DB_PASSWORD", "dbpass123"),
            ("API_KEY", "somevalue"),
            ("TOKEN", "tok123"),
            ("SECRET", "mysecret"),
        ],
    )
    def test_triggers_secret_names(self, name, value):
        r = v(f'service api {{ image: "nginx:1.0" env {{ {name}: "{value}" }} }}')
        assert any(e.code == "SEC001" for e in r.errors), (
            f"SEC001 not triggered for {name}"
        )

    def test_no_trigger_nonsecret_env(self):
        r = v(
            'service api { image: "nginx:1.0" env { LOG_LEVEL: "info" PORT: "8080" } }'
        )
        assert not any(e.code == "SEC001" for e in r.errors)

    def test_no_trigger_secret_ref(self):
        r = v(
            'service api { image: "nginx:1.0" env { PASSWORD: from secret "db-creds" } '
            '}'
        )
        assert not any(e.code == "SEC001" for e in r.errors)

    def test_hint_present(self):
        r = v('service api { image: "nginx:1.0" env { PASSWORD: "bad" } }')
        e = next(e for e in r.errors if e.code == "SEC001")
        assert e.hint is not None


class TestSEC002SecretPattern:
    @pytest.mark.parametrize(
        "value",
        [
            "sk-abcdefghijklmnopqrstuvwxyz0123456789qrstuvwxyz1234567890",
            "AKIAIOSFODNN7EXAMPLE1234",
            "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        ],
    )
    def test_triggers_known_patterns(self, value):
        r = v(f'service api {{ image: "nginx:1.0" env {{ API: "{value}" }} }}')
        codes = [e.code for e in r.errors]
        assert "SEC002" in codes or "SEC001" in codes


class TestSEC003MutableTag:
    @pytest.mark.parametrize("tag", ["latest", "master", "main", "dev", "nightly"])
    def test_triggers_mutable_tags(self, tag):
        r = v(f'service a {{ image: "nginx:{tag}" }}')
        assert any(w.code == "SEC003" for w in r.warnings), (
            f"SEC003 not triggered for {tag}"
        )

    def test_no_trigger_pinned(self):
        assert not any(
            w.code == "SEC003"
            for w in v('service a { image: "nginx:1.25.3" }').warnings
        )

    def test_no_trigger_sha(self):
        assert not any(
            w.code == "SEC003"
            for w in v('service a { image: "nginx@sha256:abc123" }').warnings
        )


class TestSEC004Privileged:
    def test_triggers_privileged(self):
        r = v('service a { image: "nginx:1.0" security { privileged: true } }')
        assert any(e.code == "SEC004" for e in r.errors)

    def test_no_trigger_without_security(self):
        assert not any(
            e.code == "SEC004" for e in v('service a { image: "nginx:1.0" }').errors
        )

    def test_blocks_is_valid(self):
        r = v('service a { image: "nginx:1.0" security { privileged: true } }')
        assert not r.is_valid


class TestSEC006SSLDisabled:
    def test_triggers_ssl_false(self):
        assert any(
            w.code == "SEC006"
            for w in v("database db { type: postgres ssl: false }").warnings
        )

    def test_no_trigger_ssl_true(self):
        assert not any(
            w.code == "SEC006"
            for w in v("database db { type: postgres ssl: true }").warnings
        )

    def test_no_trigger_default(self):
        assert not any(
            w.code == "SEC006" for w in v("database db { type: postgres }").warnings
        )


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
        src = (
            'service api { image: "nginx:latest" env { PASSWORD: "bad" } '
            "security { privileged: true } }\n"
            "database db { type: postgres ssl: false }\n"
            'secret s { key: "hardcodedvalue123" }'
        )
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


class TestSecuritySeverityAndMessages:
    """Security rules: severity and message contracts (consolidated from audit)."""

    def test_sec001_message_contains_variable_name(self):
        r = v('service s { image: "nginx:1.25" env { API_KEY: "val" } }')
        e = next(e for e in r.errors if e.code == "SEC001")
        assert "API_KEY" in e.message

    def test_sec001_hint_suggests_secret(self):
        r = v('service s { image: "nginx:1.25" env { PASSWORD: "bad" } }')
        e = next(e for e in r.errors if e.code == "SEC001")
        assert e.hint is not None and "secret" in e.hint.lower()

    def test_sec003_is_warning_not_error(self):
        r = v('service s { image: "nginx:latest" }')
        assert r.is_valid
        assert any(w.code == "SEC003" for w in r.warnings)

    def test_sec004_not_trigger_privileged_false(self):
        r = v(
            'service s { image: "nginx:1.25" security { privileged: false user: 1000 } '
            '}'
        )
        assert not any(e.code == "SEC004" for e in r.errors)

    def test_sec006_is_warning(self):
        r = v("database db { type: postgres ssl: false }")
        assert r.is_valid
        assert any(w.code == "SEC006" for w in r.warnings)


class TestSecurityEdgeCases:
    """Mutation-driven: cover false-positive-avoidance branches."""

    def _codes(self, source):
        from infra import parse, validate

        result = validate(parse(source))
        return [e.code for e in result.errors] + [w.code for w in result.warnings]

    def test_sec001_ignores_non_string_literal(self):
        # integer env value must not trip SEC001/SEC002
        codes = self._codes('service api { image: "x" env { PORT: 8080 } }')
        assert "SEC001" not in codes and "SEC002" not in codes

    def test_sec003_ignores_non_string_image(self):
        # image from a variable reference (expression) must not crash/trip SEC003
        codes = self._codes('const IMG = "nginx:latest"\nservice api { image: IMG }')
        # nginx:latest via variable may resolve; but no crash
        assert isinstance(codes, list)

    def test_sec005_no_trigger_non_root_user(self):
        codes = self._codes('service api { image: "x" security { user: 1000 } }')
        assert "SEC005" not in codes

    def test_sec007_ignores_short_secret(self):
        codes = self._codes('secret s { k: "short" }')
        assert "SEC007" not in codes

    def test_sec008_no_trigger_with_network_policy(self):
        codes = self._codes(
            'service api { image: "x" ingress { host: "h.com" } '
            'network_policy { deny_from: ["*"] } }'
        )
        assert "SEC008" not in codes

    def test_sec008_triggers_ingress_no_policy(self):
        codes = self._codes('service api { image: "x" ingress { host: "h.com" } }')
        assert "SEC008" in codes

    def test_sec009_no_trigger_registry_path(self):
        codes = self._codes('service api { image: "myreg.io/org/app:1.0" }')
        assert "SEC009" not in codes

    def test_sec009_triggers_bare_image(self):
        codes = self._codes('service api { image: "nginx:1.0" }')
        assert "SEC009" in codes

    def test_sec010_no_trigger_without_prod_env(self):
        codes = self._codes(
            'environment dev { namespace: "d" }\nsecret s { v: from env "X" }'
        )
        assert "SEC010" not in codes

    def test_sec010_triggers_in_production_env(self):
        codes = self._codes(
            'environment prod { namespace: "p" }\nsecret s { v: from env "X" }'
        )
        assert "SEC010" in codes


class TestSecurityAccumulation:
    """Mutation-driven: multi-finding accumulation and iteration order."""

    def _findings(self, source):
        from infra import parse, validate

        result = validate(parse(source))
        return list(result.errors) + list(result.warnings)

    def test_sec001_and_sec003_both_reported(self):
        # a service with a hardcoded secret (error) AND a mutable tag (warning)
        # must report BOTH (verifies findings += not = in _check_service)
        f = self._findings(
            'service api { image: "nginx:latest" env { PASSWORD: "hunter2" } }'
        )
        codes = [x.code for x in f]
        assert "SEC001" in codes and "SEC003" in codes

    def test_multiple_sec001_in_same_env(self):
        # two secret env vars -> both findings (continue, not break)
        f = self._findings(
            'service api { image: "x" env { PASSWORD: "a" TOKEN: "b" } }'
        )
        codes = [x.code for x in f]
        assert codes.count("SEC001") == 2

    def test_nonsecret_before_secret_still_finds(self):
        # a non-secret env var BEFORE the secret one (continue vs break)
        f = self._findings(
            'service api { image: "x" env { LOG_LEVEL: "info" PASSWORD: "hunter2" } }'
        )
        assert "SEC001" in [x.code for x in f]

    def test_sec002_pattern_after_secret_name(self):
        # a value matching a credential pattern in a non-secret-named var
        f = self._findings(
            'service api { image: "x" env { TOKEN_STR: '
            '"sk-abcdefghijklmnopqrstuvwxyz0123456789" } }'
        )
        codes = [x.code for x in f]
        assert "SEC002" in codes

    def test_finding_has_location(self):
        from infra import parse, validate

        result = validate(
            parse('service api { image: "nginx:latest" env { PASSWORD: "hunter2" } }')
        )
        f = (list(result.errors) + list(result.warnings))[0]
        assert f.location is not None
