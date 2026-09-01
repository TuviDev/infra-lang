"""Session 10 - Security rules SEC008-010 (Zadanie 3)."""

from __future__ import annotations

from infra import parse, validate


def _warn_codes(src: str) -> set[str]:
    res = validate(parse(src))
    assert res.is_valid, [e.code for e in res.errors]
    return {w.code for w in res.warnings}


def _warnings(src: str):
    return validate(parse(src)).warnings


class TestSec008IngressNeedsNetworkPolicy:
    def test_trigger_with_ingress_no_np(self):
        codes = _warn_codes(
            'service api { image: "reg.io/api:1.0" '
            'ingress { host: "api.example.com" } }'
        )
        assert "SEC008" in codes

    def test_no_trigger_with_network_policy(self):
        codes = _warn_codes(
            'service api { image: "reg.io/api:1.0" '
            'ingress { host: "api.example.com" } '
            'network_policy { deny_from: ["*"] allow_from: [gateway] } }'
        )
        assert "SEC008" not in codes

    def test_no_trigger_without_ingress(self):
        codes = _warn_codes('service api { image: "reg.io/api:1.0" }')
        assert "SEC008" not in codes

    def test_message_and_hint(self):
        ws = [
            w
            for w in _warnings(
                'service api { image: "reg.io/api:1.0" ingress { host: "h" } }'
            )
            if w.code == "SEC008"
        ]
        assert ws
        assert "exposed via ingress" in ws[0].message
        assert "network_policy" in (ws[0].hint or "")


class TestSec009DockerHubImage:
    def test_trigger_for_docker_hub(self):
        codes = _warn_codes('service api { image: "nginx:1.0" }')
        assert "SEC009" in codes

    def test_trigger_for_bare_tagged(self):
        codes = _warn_codes('service api { image: "myapp:v1" }')
        assert "SEC009" in codes

    def test_no_trigger_for_registry(self):
        codes = _warn_codes('service api { image: "reg.io/nginx:1.0" }')
        assert "SEC009" not in codes

    def test_no_trigger_for_namespaced_with_slash(self):
        codes = _warn_codes('service api { image: "myorg/myapp:v1" }')
        assert "SEC009" not in codes

    def test_hint_mentions_registry(self):
        ws = [
            w
            for w in _warnings('service api { image: "nginx:1.0" }')
            if w.code == "SEC009"
        ]
        assert ws
        assert "registry" in (ws[0].hint or "").lower()

    def test_message_contains_image(self):
        ws = [
            w
            for w in _warnings('service api { image: "nginx:1.0" }')
            if w.code == "SEC009"
        ]
        assert "nginx:1.0" in ws[0].message


class TestSec010EnvSecretInProduction:
    def test_trigger_in_prod_env(self):
        codes = _warn_codes(
            'environment prod { namespace: "app-prod" }\n'
            'secret db { url: from env "DATABASE_URL" }'
        )
        assert "SEC010" in codes

    def test_trigger_for_production_name(self):
        codes = _warn_codes(
            'environment production { namespace: "app" }\n'
            'secret db { url: from env "DATABASE_URL" }'
        )
        assert "SEC010" in codes

    def test_no_trigger_in_dev_env(self):
        codes = _warn_codes(
            'environment dev { namespace: "app-dev" }\n'
            'secret db { url: from env "DATABASE_URL" }'
        )
        assert "SEC010" not in codes

    def test_no_trigger_when_no_env_secret(self):
        codes = _warn_codes(
            'environment prod { namespace: "app-prod" }\n'
            'secret db { url: from vault "secret/db" }'
        )
        assert "SEC010" not in codes


class TestNewFindingsHaveHint:
    def test_all_new_rules_have_hint(self):
        src = (
            'environment prod { namespace: "app-prod" }\n'
            'service api { image: "nginx:1.0" ingress { host: "h" } }\n'
            'secret db { url: from env "DATABASE_URL" }'
        )
        for w in _warnings(src):
            if w.code in {"SEC008", "SEC009", "SEC010"}:
                assert w.hint, f"{w.code} is missing a hint"


class TestMultipleSimultaneousFindings:
    """A single service can trip several SEC rules at once; all must report."""

    def test_sec001_002_003_009_all_fire(self):
        # hardcoded secret name (SEC001), credential-pattern value on a non-secret
        # name (SEC002), mutable tag latest (SEC003 warning), docker hub (SEC009)
        src = (
            "service api {\n"
            '    image: "nginx:latest"\n'
            "    env {\n"
            '        PASSWORD: "s3cr3t-value"\n'
            '        OPENAI_KEY: "sk-abcdefghijklmnopqrstuvwxyz1234"\n'
            "    }\n"
            "}\n"
        )
        res = validate(parse(src))
        codes = {e.code for e in res.errors}
        warn_codes = {w.code for w in res.warnings}
        assert "SEC001" in codes
        assert "SEC002" in codes
        assert "SEC003" in warn_codes
        assert "SEC009" in warn_codes

    def test_sec001_and_sec002_two_entries(self):
        # one entry with a secret name (SEC001) and a separate entry whose value
        # matches a credential pattern (SEC002)
        src = (
            "service api {\n"
            '    image: "nginx"\n'
            "    env {\n"
            '        PASSWORD: "supersecret123"\n'
            '        OPENAI_KEY: "sk-abcdefghijklmnopqrstuvwxyz1234"\n'
            "    }\n"
            "}\n"
        )
        res = validate(parse(src))
        codes = {e.code for e in res.errors}
        assert "SEC001" in codes
        assert "SEC002" in codes

    def test_sec004_and_sec005_same_service(self):
        src = 'service api { image: "nginx" security { privileged: true user: 0 } }'
        res = validate(parse(src))
        err_codes = {e.code for e in res.errors}
        warn_codes = {w.code for w in res.warnings}
        assert "SEC004" in err_codes
        assert "SEC005" in warn_codes


class TestErrorSeverityBlocksCompile:
    """Error-severity findings must mark the program invalid (block compile)."""

    def _is_valid(self, src):
        return validate(parse(src)).is_valid

    def test_sec001_makes_invalid(self):
        assert not self._is_valid(
            'service api { image: "nginx" env { PASSWORD: "x" } }'
        )

    def test_sec004_makes_invalid(self):
        assert not self._is_valid(
            'service api { image: "nginx" security { privileged: true } }'
        )

    def test_warning_only_stays_valid(self):
        assert self._is_valid('service api { image: "nginx:latest" }')

    def test_clean_service_is_valid(self):
        assert self._is_valid(
            'service api { image: "registry.example.com/nginx:v1.0.0" }'
        )


class TestSecurityEdgeCases:
    def test_secret_short_value_not_error(self):
        # values <= 8 chars are not flagged as hardcoded secrets
        res = validate(parse("secret s { k: 'short' }"))
        assert res.is_valid
        assert not [e for e in res.errors if e.code == "SEC007"]

    def test_digest_image_no_mutable_tag(self):
        img = "registry.example.com/app@sha256:0123456789abcdef0123456789abcdef"
        res = validate(parse(f'service api {{ image: "{img}" }}'))
        assert not [w for w in res.warnings if w.code == "SEC003"]

    def test_registry_image_no_sec009(self):
        res = validate(parse('service api { image: "registry.example.com/nginx:1.0" }'))
        assert not [w for w in res.warnings if w.code == "SEC009"]

    def test_sec010_env_secret_in_production(self):
        src = 'environment prod { }\nsecret db { password: from env "DB_PASS" }\n'
        res = validate(parse(src))
        assert any(w.code == "SEC010" for w in res.warnings)

    def test_sec010_no_trigger_without_prod_env(self):
        src = 'secret db { password: from env "DB_PASS" }\n'
        res = validate(parse(src))
        assert not any(w.code == "SEC010" for w in res.warnings)

    def test_ssl_true_or_unset_no_sec006(self):
        assert validate(parse("database d { type: postgres ssl: true }")).is_valid
        assert validate(parse("database d { type: postgres }")).is_valid
