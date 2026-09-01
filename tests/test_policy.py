"""Tests for `infra policy-check` and the YAML policy engine (v0.7.0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from infra.cli.main import app
from infra.parser import parse
from infra.policy.engine import (
    RULE_TYPES,
    PolicyError,
    evaluate_policy,
    load_policy,
)

runner = CliRunner()

APP = (
    'service api {\n    image: "myapp:1.0"\n    replicas: 2\n}\n'
    'database db {\n    type: "postgres"\n}\n'
)

INSECURE = (
    'service api {\n'
    '    image: "myapp:latest"\n'
    "    env {\n"
    '        PASSWORD: "hardcoded123"\n'
    "    }\n"
    "}\n"
)

POLICY_ALL = (
    "version: 1\n"
    "name: team-guardrails\n"
    "rules:\n"
    "  - id: total-budget\n"
    "    type: max_monthly_cost\n"
    "    usd: 500\n"
    "  - id: service-budget\n"
    "    type: max_service_cost\n"
    "    usd: 150\n"
    "  - id: no-secrets\n"
    "    type: disallow_secret_env\n"
    "  - id: no-latest\n"
    "    type: disallow_image_tag\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


class TestLoadPolicy:
    def test_valid_policy(self, tmp_path):
        policy = load_policy(_write(tmp_path, "infra-policy.yaml", POLICY_ALL))
        assert policy.name == "team-guardrails"
        assert len(policy.rules) == 4
        assert policy.rules[0].rule_id == "total-budget"
        assert policy.rules[0].params["usd"] == 500.0

    def test_default_rule_ids(self, tmp_path):
        policy = load_policy(
            _write(
                tmp_path,
                "p.yaml",
                "rules:\n  - type: max_monthly_cost\n    usd: 1\n",
            )
        )
        assert policy.rules[0].rule_id == "max_monthly_cost#1"

    def test_rule_types_constant(self):
        assert "max_monthly_cost" in RULE_TYPES
        assert "disallow_image_tag" in RULE_TYPES

    def test_bad_yaml(self, tmp_path):
        with pytest.raises(PolicyError, match="cannot parse YAML"):
            load_policy(_write(tmp_path, "p.yaml", ":\n - ["))

    def test_top_level_not_mapping(self, tmp_path):
        with pytest.raises(PolicyError, match="expected a mapping"):
            load_policy(_write(tmp_path, "p.yaml", "- a\n- b\n"))

    def test_rules_missing(self, tmp_path):
        with pytest.raises(PolicyError, match="non-empty list"):
            load_policy(_write(tmp_path, "p.yaml", "version: 1\n"))

    def test_rules_empty(self, tmp_path):
        with pytest.raises(PolicyError, match="non-empty list"):
            load_policy(_write(tmp_path, "p.yaml", "rules: []\n"))

    def test_rule_not_mapping(self, tmp_path):
        with pytest.raises(PolicyError, match="must be a mapping"):
            load_policy(_write(tmp_path, "p.yaml", "rules: [42]\n"))

    def test_unknown_rule_type(self, tmp_path):
        with pytest.raises(PolicyError, match="unknown rule type"):
            load_policy(
                _write(tmp_path, "p.yaml", "rules:\n  - type: opa_rego\n")
            )

    def test_id_must_be_string(self, tmp_path):
        with pytest.raises(PolicyError, match="'id' must be a string"):
            load_policy(
                _write(
                    tmp_path,
                    "p.yaml",
                    "rules:\n  - id: 7\n    type: disallow_secret_env\n",
                )
            )

    def test_usd_required(self, tmp_path):
        with pytest.raises(PolicyError, match="'usd' must be a number"):
            load_policy(
                _write(tmp_path, "p.yaml", "rules:\n  - type: max_monthly_cost\n")
            )

    def test_usd_negative_rejected(self, tmp_path):
        with pytest.raises(PolicyError, match="must not be negative"):
            load_policy(
                _write(
                    tmp_path,
                    "p.yaml",
                    "rules:\n  - type: max_service_cost\n    usd: -1\n",
                )
            )

    def test_bool_usd_rejected(self, tmp_path):
        with pytest.raises(PolicyError, match="must be a number"):
            load_policy(
                _write(
                    tmp_path,
                    "p.yaml",
                    "rules:\n  - type: max_monthly_cost\n    usd: true\n",
                )
            )

    def test_names_must_be_str_list(self, tmp_path):
        with pytest.raises(PolicyError, match="must be a list of strings"):
            load_policy(
                _write(
                    tmp_path,
                    "p.yaml",
                    "rules:\n  - type: disallow_secret_env\n    names: [1, 2]\n",
                )
            )

    def test_tags_optional(self, tmp_path):
        policy = load_policy(
            _write(tmp_path, "p.yaml", "rules:\n  - type: disallow_image_tag\n")
        )
        assert policy.rules[0].params["tags"] == []


class TestEvaluatePolicy:
    def _policy(self, tmp_path, text=POLICY_ALL):
        return load_policy(_write(tmp_path, "p.yaml", text))

    def test_clean_file_passes(self, tmp_path):
        violations = evaluate_policy(parse(APP), self._policy(tmp_path))
        assert violations == []

    def test_total_budget_violation_pol001(self, tmp_path):
        policy = self._policy(
            tmp_path, "rules:\n  - type: max_monthly_cost\n    usd: 0.01\n"
        )
        violations = evaluate_policy(parse(APP), policy)
        assert len(violations) == 1
        assert violations[0].code == "POL001"
        assert violations[0].resource is None
        assert "exceeds" in violations[0].message

    def test_per_service_budget_violation_pol002(self, tmp_path):
        policy = self._policy(
            tmp_path, "rules:\n  - type: max_service_cost\n    usd: 0.01\n"
        )
        violations = evaluate_policy(parse(APP), policy)
        assert violations
        assert all(v.code == "POL002" for v in violations)
        assert {v.resource for v in violations} >= {"api", "db"}

    def test_secret_env_violation_pol003(self, tmp_path):
        policy = self._policy(
            tmp_path, "rules:\n  - type: disallow_secret_env\n"
        )
        violations = evaluate_policy(parse(INSECURE), policy)
        assert len(violations) == 1
        assert violations[0].code == "POL003"
        assert violations[0].resource == "api"
        assert "PASSWORD" in violations[0].message

    def test_secret_env_from_secret_reference_ok(self, tmp_path):
        src = (
            "service api {\n"
            '    image: "x:1"\n'
            "    env {\n"
            '        PASSWORD: from secret "db".password\n'
            "    }\n"
            "}\n"
            'secret db {\n    password: "x"\n}\n'
        )
        policy = self._policy(
            tmp_path, "rules:\n  - type: disallow_secret_env\n"
        )
        assert evaluate_policy(parse(src), policy) == []

    def test_custom_env_names(self, tmp_path):
        policy = self._policy(
            tmp_path,
            "rules:\n"
            "  - type: disallow_secret_env\n"
            "    names: [my_custom_token]\n",
        )
        src = (
            "service api {\n"
            '    image: "x:1"\n'
            "    env {\n"
            '        MY_CUSTOM_TOKEN: "abc123"\n'
            "    }\n"
            "}\n"
        )
        violations = evaluate_policy(parse(src), policy)
        assert len(violations) == 1

    def test_latest_tag_violation_pol004(self, tmp_path):
        policy = self._policy(
            tmp_path, "rules:\n  - type: disallow_image_tag\n"
        )
        violations = evaluate_policy(parse(INSECURE), policy)
        assert len(violations) == 1
        assert violations[0].code == "POL004"
        assert "latest" in violations[0].message

    def test_untagged_image_is_implicit_latest(self, tmp_path):
        policy = self._policy(
            tmp_path, "rules:\n  - type: disallow_image_tag\n"
        )
        violations = evaluate_policy(
            parse('service api { image: "nginx" }'), policy
        )
        assert len(violations) == 1

    def test_custom_forbidden_tags(self, tmp_path):
        policy = self._policy(
            tmp_path,
            "rules:\n"
            "  - type: disallow_image_tag\n"
            "    tags: [dev, staging]\n",
        )
        assert evaluate_policy(
            parse('service a { image: "x:dev" }'), policy
        )
        assert not evaluate_policy(
            parse('service a { image: "x:1.0" }'), policy
        )

    def test_registry_port_not_confused_with_tag(self, tmp_path):
        registry = 'service a { image: "registry.local:5000/app:1.0" }'
        policy = self._policy(
            tmp_path, "rules:\n  - type: disallow_image_tag\n"
        )
        assert evaluate_policy(parse(registry), policy) == []

    def test_violation_to_dict(self, tmp_path):
        policy = self._policy(
            tmp_path, "rules:\n  - type: disallow_image_tag\n"
        )
        v = evaluate_policy(parse(INSECURE), policy)[0]
        data = v.to_dict()
        assert data["code"] == "POL004"
        assert data["resource"] == "api"


class TestPolicyCheckCLI:
    def test_pass_exits_0(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        p = _write(tmp_path, "infra-policy.yaml", POLICY_ALL)
        result = runner.invoke(app, ["policy-check", str(f), "--policy", str(p)])
        assert result.exit_code == 0
        assert "[OK]" in result.output
        assert "4 rule(s)" in result.output

    def test_violations_exit_1_and_codes_listed(self, tmp_path):
        f = _write(tmp_path, "app.infra", INSECURE)
        p = _write(tmp_path, "infra-policy.yaml", POLICY_ALL)
        result = runner.invoke(app, ["policy-check", str(f), "-p", str(p)])
        assert result.exit_code == 1
        assert "POL003" in result.output
        assert "POL004" in result.output
        assert "no-secrets" in result.output
        assert "[FAIL]" in result.output

    def test_json_format(self, tmp_path):
        f = _write(tmp_path, "app.infra", INSECURE)
        p = _write(tmp_path, "infra-policy.yaml", POLICY_ALL)
        result = runner.invoke(
            app, ["policy-check", str(f), "-p", str(p), "-f", "json"]
        )
        assert result.exit_code == 1
        payload = json.loads(result.output[: result.output.rindex("}") + 1])
        assert payload["passed"] is False
        assert payload["rules_checked"] == 4
        codes = {v["code"] for v in payload["violations"]}
        assert {"POL003", "POL004"} <= codes

    def test_json_pass(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        p = _write(tmp_path, "infra-policy.yaml", POLICY_ALL)
        result = runner.invoke(
            app, ["policy-check", str(f), "-p", str(p), "-f", "json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.output[: result.output.rindex("}") + 1])
        assert payload["passed"] is True

    def test_auto_discovery_in_cwd(self, tmp_path, monkeypatch):
        _write(tmp_path, "app.infra", INSECURE)
        _write(tmp_path, "infra-policy.yaml", POLICY_ALL)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["policy-check", "app.infra"])
        assert result.exit_code == 1
        assert "POL003" in result.output

    def test_no_policy_anywhere_exits_2(self, tmp_path, monkeypatch):
        _write(tmp_path, "app.infra", APP)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["policy-check", "app.infra"])
        assert result.exit_code == 2

    def test_missing_policy_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        result = runner.invoke(
            app, ["policy-check", str(f), "-p", str(tmp_path / "nope.yaml")]
        )
        assert result.exit_code == 1

    def test_invalid_policy_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        p = _write(tmp_path, "bad.yaml", "rules:\n  - type: bogus\n")
        result = runner.invoke(app, ["policy-check", str(f), "-p", str(p)])
        assert result.exit_code == 1
        assert "unknown rule type" in result.output

    def test_missing_file_exits_1(self, tmp_path):
        p = _write(tmp_path, "infra-policy.yaml", POLICY_ALL)
        result = runner.invoke(
            app, ["policy-check", str(tmp_path / "nope.infra"), "-p", str(p)]
        )
        assert result.exit_code == 1

    def test_parse_error_exits_1(self, tmp_path):
        f = _write(tmp_path, "broken.infra", "service {{\n")
        p = _write(tmp_path, "infra-policy.yaml", POLICY_ALL)
        result = runner.invoke(app, ["policy-check", str(f), "-p", str(p)])
        assert result.exit_code == 1

    def test_unknown_format_exits_1(self, tmp_path):
        f = _write(tmp_path, "app.infra", APP)
        p = _write(tmp_path, "infra-policy.yaml", POLICY_ALL)
        result = runner.invoke(
            app, ["policy-check", str(f), "-p", str(p), "-f", "yaml"]
        )
        assert result.exit_code == 1

    def test_environment_overlay(self, tmp_path):
        src = (
            'service api {\n    image: "x:1"\n    replicas: 1\n}\n'
            'environment "big" {\n    service api {\n        replicas: 9\n    }\n}\n'
        )
        policy = (
            "rules:\n  - id: svc-cap\n    type: max_service_cost\n    usd: 100\n"
        )
        f = _write(tmp_path, "app.infra", src)
        p = _write(tmp_path, "infra-policy.yaml", policy)
        base = runner.invoke(app, ["policy-check", str(f), "-p", str(p)])
        big = runner.invoke(
            app, ["policy-check", str(f), "-p", str(p), "-e", "big"]
        )
        assert base.exit_code == 0
        assert big.exit_code == 1
        assert "POL002" in big.output

    def test_help(self):
        result = runner.invoke(app, ["policy-check", "--help"])
        assert result.exit_code == 0
        assert "policy" in result.output
