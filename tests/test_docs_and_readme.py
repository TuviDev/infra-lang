"""Documentation and README validation tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from infra import parse, validate

README = Path("README.md")
DOCS = Path("docs")
EXAMPLES = Path("examples")


def infra_blocks(text):
    return re.findall(r"```(?:infra|hcl)\n(.*?)```", text, re.DOTALL)


class TestReadme:
    def test_exists_and_not_empty(self):
        assert README.exists()
        content = README.read_text(encoding="utf-8")
        assert len(content) > 500, "README too short"

    def test_has_installation_section(self):
        assert "pip install" in README.read_text(encoding="utf-8")

    def test_has_features_section(self):
        content = README.read_text(encoding="utf-8")
        assert "Feature" in content or "feature" in content

    def test_has_cli_reference(self):
        content = README.read_text(encoding="utf-8")
        assert "infra compile" in content
        assert "infra validate" in content

    def test_has_backends_info(self):
        content = README.read_text(encoding="utf-8")
        assert "Kubernetes" in content
        assert "Terraform" in content
        assert "GitHub" in content

    def test_infra_code_blocks_parseable(self):
        content = README.read_text(encoding="utf-8")
        blocks = infra_blocks(content)
        for i, block in enumerate(blocks):
            try:
                parse(block.strip())
            except Exception as e:
                pytest.fail(
                    f"README code block {i + 1} failed: {e}\nBlock:\n{block[:200]}"
                )


class TestExamples:
    def test_examples_dir_exists(self):
        assert EXAMPLES.exists()

    def test_minimum_examples(self):
        files = list(EXAMPLES.glob("*.infra"))
        assert len(files) >= 4

    @pytest.mark.parametrize(
        "f",
        list(EXAMPLES.glob("*.infra")) if EXAMPLES.exists() else [],
        ids=lambda f: f.name,
    )
    def test_example_parses(self, f):
        parse(f.read_text(encoding="utf-8"), filename=str(f))

    @pytest.mark.parametrize(
        "f",
        list(EXAMPLES.glob("*.infra")) if EXAMPLES.exists() else [],
        ids=lambda f: f.name,
    )
    def test_example_no_semantic_errors(self, f):
        result = validate(parse(f.read_text(encoding="utf-8"), filename=str(f)))
        non_sec = [e for e in result.errors if not (e.code or "").startswith("SEC")]
        assert len(non_sec) == 0, f"{f.name}: {[e.message for e in non_sec]}"


class TestLanguageSpec:
    def test_spec_exists(self):
        spec = DOCS / "language_spec.md"
        assert spec.exists()

    def test_spec_has_error_codes(self):
        spec = DOCS / "language_spec.md"
        if not spec.exists():
            pytest.skip("spec not found")
        content = spec.read_text(encoding="utf-8")
        for code in [
            "E001",
            "E002",
            "E003",
            "E004",
            "E005",
            "SEC001",
            "REL001",
            "REL002",
        ]:
            assert code in content, f"Error code {code} missing from spec"
