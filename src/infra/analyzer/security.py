"""Security lint rules.

Only rules that are always true for correctly configured infrastructure
(hardcoded secrets, mutable tags, privileged containers, root user, disabled
SSL) — zero false positives on a well-configured definition.
"""

from __future__ import annotations

import re
from typing import Any

from infra.errors.exceptions import ValidationError, ValidationWarning
from infra.parser import ast_nodes as n

SECRET_ENV_NAMES = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "private_key",
        "db_password",
        "database_url",
        "auth_token",
        "access_key",
        "secret_key",
        "client_secret",
        "database_password",
        "db_pass",
        "redis_password",
    }
)

MUTABLE_TAGS = frozenset(
    {
        "latest",
        "master",
        "main",
        "dev",
        "test",
        "nightly",
        "edge",
        "canary",
        "snapshot",
    }
)

SECRET_VALUE_PATTERNS = [
    ("openai", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("github", re.compile(r"gh[pousr]_[a-zA-Z0-9]{20,}")),
    ("aws", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("jwt", re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}")),
]


class SecurityChecker:
    def check(self, program: n.Program) -> list[Any]:
        findings = []
        for stmt in program.statements:
            if isinstance(stmt, n.ServiceDef):
                findings += self._check_service(stmt)
            elif isinstance(stmt, n.DatabaseDef):
                findings += self._check_database(stmt)
            elif isinstance(stmt, n.SecretDef):
                findings += self._check_secret_def(stmt)
        findings += self._sec010_env_secret_in_production(program)
        return findings

    def _check_service(self, svc: n.ServiceDef) -> list[Any]:
        findings = []
        findings += self._sec001_hardcoded_env(svc)
        findings += self._sec003_mutable_tag(svc)
        findings += self._sec004_privileged(svc)
        findings += self._sec005_root_user(svc)
        findings += self._sec008_ingress_no_network_policy(svc)
        findings += self._sec009_docker_hub_image(svc)
        return findings

    def _check_database(self, db: n.DatabaseDef) -> list[Any]:
        return self._sec006_ssl_disabled(db)

    def _check_secret_def(self, secret: n.SecretDef) -> list[Any]:
        return self._sec007_hardcoded_secret_value(secret)

    def _sec001_hardcoded_env(self, svc: n.ServiceDef) -> list[Any]:
        findings = []
        for entry in svc.env:
            if entry.value is None or not isinstance(entry.value, n.Literal):
                continue
            val = entry.value.value
            if not isinstance(val, str):
                continue
            name_lower = entry.name.lower()
            if name_lower in SECRET_ENV_NAMES:
                findings.append(
                    ValidationError(
                        message=(
                            f"Hardcoded secret detected: '{entry.name}' in service "
                            f"'{svc.name}' appears to contain a sensitive value."
                        ),
                        location=entry.location or svc.location,
                        code="SEC001",
                        hint=f'Use: {entry.name} from secret "your-secret-name"',
                    )
                )
                continue
            for pat_name, pattern in SECRET_VALUE_PATTERNS:
                if pattern.search(val):
                    findings.append(
                        ValidationError(
                            message=f"'{entry.name}' value matches {pat_name} credential pattern.",  # noqa: E501
                            location=entry.location or svc.location,
                            code="SEC002",
                            hint="Move this value to a secret manager.",
                        )
                    )
                    break
        return findings

    def _sec003_mutable_tag(self, svc: n.ServiceDef) -> list[Any]:
        if svc.image is None:
            return []
        img = svc.image
        if not isinstance(img, str):
            return []
        if "@sha256:" in img:
            return []
        tag = img.split(":")[-1] if ":" in img else "latest"
        if tag not in MUTABLE_TAGS:
            return []
        return [
            ValidationWarning(
                message=(
                    f"Service '{svc.name}' uses mutable image tag '{tag}'. This can "
                    "cause unexpected behavior after image push."
                ),
                location=svc.location,
                code="SEC003",
                hint=f'Use an immutable tag: "{img.split(":")[0]}:v1.0.0" or a SHA digest.',  # noqa: E501
            )
        ]

    def _sec004_privileged(self, svc: n.ServiceDef) -> list[Any]:
        if svc.security is None or not svc.security.privileged:
            return []
        return [
            ValidationError(
                message=f"Service '{svc.name}' runs in privileged mode. Full host access is a critical security risk.",  # noqa: E501
                location=svc.location,
                code="SEC004",
                hint="Remove 'privileged: true' — almost never needed in production.",
            )
        ]

    def _sec005_root_user(self, svc: n.ServiceDef) -> list[Any]:
        if svc.security is None or svc.security.user is None:
            return []
        if svc.security.user != 0:
            return []
        return [
            ValidationWarning(
                message=f"Service '{svc.name}' is configured to run as root (UID 0).",
                location=svc.location,
                code="SEC005",
                hint="Use a non-root user: user: 1000",
            )
        ]

    def _sec006_ssl_disabled(self, db: n.DatabaseDef) -> list[Any]:
        if db.ssl is not False:
            return []
        return [
            ValidationWarning(
                message=f"Database '{db.name}' has SSL explicitly disabled. All connections are unencrypted.",  # noqa: E501
                location=db.location,
                code="SEC006",
                hint="Remove ssl: false or set ssl: true",
            )
        ]

    def _sec007_hardcoded_secret_value(self, secret: n.SecretDef) -> list[Any]:
        findings = []
        for entry in secret.entries:
            if entry.value is None or not isinstance(entry.value, str):
                continue
            if len(entry.value) > 8:
                findings.append(
                    ValidationError(
                        message=(
                            f"Secret entry '{entry.name}' in '{secret.name}' has a "
                            "hardcoded value. Never store actual secrets in source files."  # noqa: E501
                        ),
                        location=entry.location or secret.location,
                        code="SEC007",
                        hint=f'Use: {entry.name} from env "{entry.name.upper()}"',
                    )
                )
        return findings

    def _sec008_ingress_no_network_policy(self, svc: n.ServiceDef) -> list[Any]:
        if svc.ingress is None or svc.network_policy is not None:
            return []
        return [
            ValidationWarning(
                message=(
                    f"Service '{svc.name}' is exposed via ingress but has no "
                    "network_policy. Any pod can reach it."
                ),
                location=svc.location,
                code="SEC008",
                hint="Add network_policy { deny_from: ['*'] allow_from: [gateway] }",
            )
        ]

    def _sec009_docker_hub_image(self, svc: n.ServiceDef) -> list[Any]:
        img = svc.image
        if not isinstance(img, str) or not img:
            return []
        # An image with a path segment ("registry/org/name") is assumed to use a
        # (possibly private) registry; only a bare image such as "nginx:1.0" is
        # pulled from the un-auditable public Docker Hub.
        if "/" in img:
            return []
        return [
            ValidationWarning(
                message=(
                    f"Image '{img}' uses Docker Hub (no registry prefix). "
                    "Consider using a private registry."
                ),
                location=svc.location,
                code="SEC009",
                hint="Use: registry.example.com/nginx:1.0",
            )
        ]

    def _sec010_env_secret_in_production(self, program: n.Program) -> list[Any]:
        env_names = {
            e.name.lower()
            for e in program.statements
            if isinstance(e, n.EnvironmentDef)
        }
        if not (env_names & {"prod", "production"}):
            return []
        findings = []
        for stmt in program.statements:
            if not isinstance(stmt, n.SecretDef):
                continue
            for entry in stmt.entries:
                if entry.from_env is not None:
                    findings.append(
                        ValidationWarning(
                            message=(
                                f"Secret '{stmt.name}' uses env var source. In "
                                "production consider vault or secret manager."
                            ),
                            location=entry.location or stmt.location,
                            code="SEC010",
                            hint="Use: key from vault 'path/to/secret'",
                        )
                    )
        return findings
