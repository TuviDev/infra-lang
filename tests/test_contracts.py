"""
Contract tests for public-facing syntax.

These tests guarantee that every code example in public documentation parses
and validates correctly. If any of these fail, documentation is inconsistent
with the parser.

DO NOT modify these tests to match a broken parser.
FIX THE PARSER (or the docs) to make them pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from infra import parse, validate

CONTRACTS_DIR = Path("tests/contracts")
PUBLIC_DOCS = [
    Path("README.md"),
    Path("docs/tutorial.md"),
    Path("docs/language_spec.md"),
    Path("docs/language_decisions.md"),
    Path("docs/quickstart.md"),
    Path("docs/support_matrix.md"),
    Path("docs/versioning.md"),
    Path("docs/lsp.md"),
    Path("docs/feedback_policy.md"),
    Path("docs/known_limitations.md"),
    Path("docs/troubleshooting.md"),
    Path("docs/roadmap_v0.2.0.md"),
]
EXAMPLES_DIR = Path("examples")


def extract_infra_blocks(text: str, source: str) -> list[tuple[str, str]]:
    """Extract ```infra blocks with their source info."""
    pattern = r"```infra\n(.*?)```"
    blocks = re.findall(pattern, text, re.DOTALL)
    return [(b.strip(), source) for b in blocks if b.strip()]


def get_all_contract_blocks():
    blocks = []
    for doc in PUBLIC_DOCS:
        if doc.exists():
            for block, src in extract_infra_blocks(
                doc.read_text(encoding="utf-8"), str(doc)
            ):
                blocks.append((block, src))
    return blocks


_ALL_BLOCKS = get_all_contract_blocks()


@pytest.mark.contracts
class TestPublicDocumentationSyntax:
    """Every infra code block in public docs must parse."""

    @pytest.mark.parametrize(
        "block,source",
        _ALL_BLOCKS,
        ids=[f"{Path(s).name}:{i}" for i, (_, s) in enumerate(_ALL_BLOCKS)],
    )
    def test_public_block_parses(self, block, source):
        try:
            parse(block, filename=source)
        except Exception as e:
            pytest.fail(
                f"Public example in {source} fails to parse:\n{block[:200]}\nError: {e}"
            )


@pytest.mark.contracts
class TestExamplesDirectory:
    """Every file in examples/ must be valid."""

    @pytest.mark.parametrize(
        "example_file",
        list(EXAMPLES_DIR.glob("**/*.infra")) if EXAMPLES_DIR.exists() else [],
        ids=lambda f: str(f.relative_to(EXAMPLES_DIR)),
    )
    def test_example_parses(self, example_file):
        parse(example_file.read_text(encoding="utf-8"), filename=str(example_file))

    @pytest.mark.parametrize(
        "example_file",
        [
            f
            for f in EXAMPLES_DIR.glob("**/*.infra")
            if "demo" in str(f) or f.name.startswith(("0", "1", "2"))
        ]
        if EXAMPLES_DIR.exists()
        else [],
        ids=lambda f: str(f.relative_to(EXAMPLES_DIR)),
    )
    def test_example_has_no_semantic_errors(self, example_file):
        result = validate(parse(example_file.read_text(encoding="utf-8")))
        non_sec = [e for e in result.errors if not (e.code or "").startswith("SEC")]
        assert len(non_sec) == 0, (
            f"{example_file.name} has errors: {[e.message for e in non_sec]}"
        )
