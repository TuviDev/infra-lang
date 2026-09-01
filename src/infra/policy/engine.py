"""Declarative YAML policy engine (``infra-policy.yaml``).

Policies live **outside** the .infra DSL (no grammar changes) as plain YAML:

.. code-block:: yaml

    version: 1
    rules:
      - id: total-budget
        type: max_monthly_cost
        usd: 500

      - id: service-budget
        type: max_service_cost
        usd: 120

      - id: no-secrets-in-env
        type: disallow_secret_env
        names: [my_custom_token]        # optional, merged with the SEC list

      - id: no-latest
        type: disallow_image_tag
        tags: [latest, dev]             # optional, default: [latest]

Every violation carries a stable code (POL001..POL004) so CI systems can
filter on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.analyzer.cost import estimate_cost
from infra.analyzer.security import SECRET_ENV_NAMES
from infra.parser import ast_nodes as n

#: Supported rule types (the YAML ``type:`` field).
RULE_MAX_MONTHLY_COST = "max_monthly_cost"
RULE_MAX_SERVICE_COST = "max_service_cost"
RULE_DISALLOW_SECRET_ENV = "disallow_secret_env"
RULE_DISALLOW_IMAGE_TAG = "disallow_image_tag"
RULE_TYPES = (
    RULE_MAX_MONTHLY_COST,
    RULE_MAX_SERVICE_COST,
    RULE_DISALLOW_SECRET_ENV,
    RULE_DISALLOW_IMAGE_TAG,
)

#: Stable violation codes per rule type.
_RULE_CODES = {
    RULE_MAX_MONTHLY_COST: "POL001",
    RULE_MAX_SERVICE_COST: "POL002",
    RULE_DISALLOW_SECRET_ENV: "POL003",
    RULE_DISALLOW_IMAGE_TAG: "POL004",
}

#: Tags forbidden by ``disallow_image_tag`` when no explicit list is given.
DEFAULT_FORBIDDEN_TAGS = ("latest",)


class PolicyError(Exception):
    """Raised when a policy file cannot be loaded or is invalid."""


@dataclass
class PolicyRule:
    """One validated rule from a policy file."""

    type: str
    rule_id: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Policy:
    """A validated policy file."""

    name: str = ""
    rules: List[PolicyRule] = field(default_factory=list)


@dataclass
class PolicyViolation:
    """A single rule breach found in a .infra program."""

    code: str
    rule_id: str
    message: str
    resource: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "rule_id": self.rule_id,
            "resource": self.resource,
            "message": self.message,
        }


@dataclass
class PolicyResult:
    """Outcome of evaluating a policy against a program."""

    source: str
    policy: str
    rules_checked: int
    violations: List[PolicyViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "policy": self.policy,
            "rules_checked": self.rules_checked,
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
        }


# --------------------------------------------------------------------------- #
# Loading & validation
# --------------------------------------------------------------------------- #


def _require_number(rule: Dict[str, Any], key: str, *, where: str) -> float:
    value = rule.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError(f"{where}: '{key}' must be a number (USD)")
    if value < 0:
        raise PolicyError(f"{where}: '{key}' must not be negative")
    return float(value)


def _optional_str_list(rule: Dict[str, Any], key: str, *, where: str) -> List[str]:
    value = rule.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyError(f"{where}: '{key}' must be a list of strings")
    return list(value)


def load_policy(path: Path) -> Policy:
    """Load and validate a YAML policy file; raise ``PolicyError`` if bad."""
    from ruamel.yaml import YAML

    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PolicyError(f"{path}: cannot parse YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path}: expected a mapping at the top level")

    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise PolicyError(f"{path}: 'rules' must be a non-empty list")

    rules: List[PolicyRule] = []
    for index, raw in enumerate(raw_rules):
        where = f"{path}: rules[{index}]"
        if not isinstance(raw, dict):
            raise PolicyError(f"{where} must be a mapping")
        rule_type = raw.get("type")
        if rule_type not in RULE_TYPES:
            raise PolicyError(
                f"{where}: unknown rule type {rule_type!r}; "
                f"valid: {list(RULE_TYPES)}"
            )
        rule_id = raw.get("id")
        if rule_id is not None and not isinstance(rule_id, str):
            raise PolicyError(f"{where}: 'id' must be a string")

        params: Dict[str, Any] = {}
        if rule_type in (RULE_MAX_MONTHLY_COST, RULE_MAX_SERVICE_COST):
            params["usd"] = _require_number(raw, "usd", where=where)
        elif rule_type == RULE_DISALLOW_SECRET_ENV:
            params["names"] = _optional_str_list(raw, "names", where=where)
        elif rule_type == RULE_DISALLOW_IMAGE_TAG:
            params["tags"] = _optional_str_list(raw, "tags", where=where)

        rules.append(
            PolicyRule(
                type=rule_type,
                rule_id=rule_id or f"{rule_type}#{index + 1}",
                params=params,
            )
        )

    name = data.get("name")
    return Policy(
        name=name if isinstance(name, str) else "",
        rules=rules,
    )


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _tag_of(image: str) -> str:
    """Extract an image tag; a tagless image means an implicit ``latest``."""
    tail = image.rsplit("/", 1)[-1]
    if ":" in tail:
        return tail.rsplit(":", 1)[-1].lower()
    return "latest"


def _services(program: n.Program) -> List[n.ServiceDef]:
    return [s for s in program.statements if isinstance(s, n.ServiceDef)]


def _check_cost_total(
    rule: PolicyRule, program: n.Program
) -> List[PolicyViolation]:
    limit = rule.params["usd"]
    total = estimate_cost(program).total_monthly_usd
    if total <= limit:
        return []
    return [
        PolicyViolation(
            code=_RULE_CODES[rule.type],
            rule_id=rule.rule_id,
            resource=None,
            message=(
                f"total monthly cost ${total:.2f} exceeds the policy limit "
                f"of ${limit:.2f}"
            ),
        )
    ]


def _check_cost_per_service(
    rule: PolicyRule, program: n.Program
) -> List[PolicyViolation]:
    limit = rule.params["usd"]
    violations: List[PolicyViolation] = []
    for item in estimate_cost(program).items:
        if item.monthly_usd > limit:
            violations.append(
                PolicyViolation(
                    code=_RULE_CODES[rule.type],
                    rule_id=rule.rule_id,
                    resource=item.name,
                    message=(
                        f"{item.kind} '{item.name}' costs "
                        f"${item.monthly_usd:.2f}/mo, above the per-service "
                        f"limit of ${limit:.2f}"
                    ),
                )
            )
    return violations


def _check_secret_env(
    rule: PolicyRule, program: n.Program
) -> List[PolicyViolation]:
    names = {n_.lower() for n_ in SECRET_ENV_NAMES}
    names.update(n_.lower() for n_ in rule.params.get("names", []))
    violations: List[PolicyViolation] = []
    for svc in _services(program):
        for entry in svc.env:
            value = entry.value
            if not isinstance(value, n.Literal) or not isinstance(
                value.value, str
            ):
                continue  # `from secret ...` references are fine
            if entry.name.lower() in names:
                violations.append(
                    PolicyViolation(
                        code=_RULE_CODES[rule.type],
                        rule_id=rule.rule_id,
                        resource=svc.name,
                        message=(
                            f"service '{svc.name}' hardcodes secret env var "
                            f"'{entry.name}' — use `from secret` instead"
                        ),
                    )
                )
    return violations


def _check_image_tag(
    rule: PolicyRule, program: n.Program
) -> List[PolicyViolation]:
    forbidden = {
        t.lower() for t in (rule.params.get("tags") or DEFAULT_FORBIDDEN_TAGS)
    }
    violations: List[PolicyViolation] = []
    for svc in _services(program):
        if svc.image is None:
            continue  # build-from-source services carry no image tag
        tag = _tag_of(svc.image)
        if tag in forbidden:
            violations.append(
                PolicyViolation(
                    code=_RULE_CODES[rule.type],
                    rule_id=rule.rule_id,
                    resource=svc.name,
                    message=(
                        f"service '{svc.name}' uses forbidden image tag "
                        f"'{tag}' ({svc.image})"
                    ),
                )
            )
    return violations


_CHECKS = {
    RULE_MAX_MONTHLY_COST: _check_cost_total,
    RULE_MAX_SERVICE_COST: _check_cost_per_service,
    RULE_DISALLOW_SECRET_ENV: _check_secret_env,
    RULE_DISALLOW_IMAGE_TAG: _check_image_tag,
}


def evaluate_policy(program: n.Program, policy: Policy) -> List[PolicyViolation]:
    """Evaluate every rule of *policy* against *program*."""
    violations: List[PolicyViolation] = []
    for rule in policy.rules:
        violations.extend(_CHECKS[rule.type](rule, program))
    return violations
