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
        ws = [w for w in _warnings(
            'service api { image: "reg.io/api:1.0" ingress { host: "h" } }'
        ) if w.code == "SEC008"]
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
        ws = [w for w in _warnings('service api { image: "nginx:1.0" }')
              if w.code == "SEC009"]
        assert ws
        assert "registry" in (ws[0].hint or "").lower()

    def test_message_contains_image(self):
        ws = [w for w in _warnings('service api { image: "nginx:1.0" }')
              if w.code == "SEC009"]
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
