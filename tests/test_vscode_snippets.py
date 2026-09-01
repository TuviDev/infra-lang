"""VS Code snippets for v0.7.x/v0.8.0 language constructs (v0.8.0).

Every snippet body must still parse as a valid .infra program once its
placeholders are substituted with their defaults — this keeps the
snippets honest against grammar evolution.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EXT = Path(__file__).resolve().parent.parent / "vscode-infra-lang"
SNIPPETS = EXT / "snippets" / "infra.json"

_REQUIRED_PREFIXES = [
    "infra-netpol",
    "infra-secstore",
    "infra-autoscale",
    "infra-schedule",
    "infra-disruption",
    "infra-schema",
]


def _load() -> dict:
    return json.loads(SNIPPETS.read_text(encoding="utf-8"))


def _with_defaults(body_lines: list) -> str:
    """Replace VS Code placeholders with their first defaults."""
    text = "\n".join(body_lines)
    text = re.sub(r"\$\{\d+\|([^}]*)\|}", lambda m: m.group(1).split(",")[0], text)
    text = re.sub(r"\$\{\d+:([^}]*)\}", r"\1", text)
    return re.sub(r"\$\d+", "", text)


class TestNewSnippetPrefixes:
    def test_all_v080_prefixes_present(self):
        prefixes = {s["prefix"] for s in _load().values()}
        for prefix in _REQUIRED_PREFIXES:
            assert prefix in prefixes, f"missing snippet prefix: {prefix}"

    def test_snippet_count_grew(self):
        assert len(_load()) >= 18  # 12 legacy + 6 new

    def test_netpol_uses_allow_and_deny(self):
        snippets = _load()
        netpol = next(s for s in snippets.values() if s["prefix"] == "infra-netpol")
        body = "\n".join(netpol["body"])
        assert "allow_from" in body
        assert "deny_from" in body

    def test_secstore_offers_provider_choice(self):
        snippets = _load()
        secstore = next(s for s in snippets.values() if s["prefix"] == "infra-secstore")
        body = "\n".join(secstore["body"])
        for provider in ("vault", "aws", "gcp", "k8s"):
            assert provider in body


class TestSnippetsStayValid:
    def test_new_snippets_parse_and_validate_with_defaults(self):
        from infra.analyzer.validator import SemanticValidator
        from infra.parser import parse

        snippets = _load()
        for prefix in _REQUIRED_PREFIXES:
            snippet = next(s for s in snippets.values() if s["prefix"] == prefix)
            source = _with_defaults(snippet["body"])
            program = parse(source)
            result = SemanticValidator().validate(program)
            assert result.errors == [], (
                f"snippet {prefix} no longer compiles: "
                f"{[e.code for e in result.errors]}"
            )

    def test_every_snippet_has_scope_infra(self):
        for name, snippet in _load().items():
            assert snippet.get("scope") == "infra", name
            assert snippet.get("prefix"), name
            assert snippet.get("body"), name
