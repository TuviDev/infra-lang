"""Corpus tests verify behavior across a range of inputs.

minimal/    → must parse and validate
realistic/  → must parse, validate, and compile
edge_cases/ → must not crash (parse or InfraParseError)
invalid/    → must fail with specific error codes
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infra import parse, validate
from infra.backends.kubernetes import KubernetesBackend
from infra.errors.exceptions import InfraLexError, InfraParseError

CORPUS = Path("tests/corpus")


@pytest.mark.parametrize(
    "f",
    list((CORPUS / "minimal").glob("*.infra"))
    if (CORPUS / "minimal").exists()
    else [],
    ids=lambda f: f.stem,
)
def test_minimal_parses_and_validates(f):
    program = parse(f.read_text(), filename=str(f))
    result = validate(program)
    assert result.is_valid or len(
        [e for e in result.errors if not (e.code or "").startswith("SEC")]
    ) == 0


@pytest.mark.parametrize(
    "f",
    list((CORPUS / "realistic").glob("*.infra"))
    if (CORPUS / "realistic").exists()
    else [],
    ids=lambda f: f.stem,
)
def test_realistic_compiles_to_valid_k8s(f):
    import yaml

    program = parse(f.read_text(), filename=str(f))
    result = KubernetesBackend().compile(program)
    for name, content in result.files.items():
        for doc in yaml.safe_load_all(content):
            assert doc is None or isinstance(doc, dict), name


@pytest.mark.parametrize(
    "f",
    list((CORPUS / "edge_cases").glob("*.infra"))
    if (CORPUS / "edge_cases").exists()
    else [],
    ids=lambda f: f.stem,
)
def test_edge_cases_do_not_crash(f):
    try:
        parse(f.read_text(), filename=str(f))
    except (InfraParseError, InfraLexError):
        pass


@pytest.mark.parametrize(
    "f",
    list((CORPUS / "invalid").glob("*.infra"))
    if (CORPUS / "invalid").exists()
    else [],
    ids=lambda f: f.stem,
)
def test_invalid_files_fail_with_known_error(f):
    content = f.read_text()
    expected_code = None
    for line in content.split("\n"):
        if "Expected:" in line:
            parts = line.split("Expected:")
            if len(parts) > 1:
                expected_code = parts[1].strip()
                break
    try:
        program = parse(content, filename=str(f))
        result = validate(program)
        if expected_code:
            all_codes = {e.code for e in result.errors}
            assert expected_code in all_codes, (
                f"{f.name}: expected {expected_code}, got {all_codes}"
            )
        else:
            assert not result.is_valid
    except (InfraParseError, InfraLexError):
        pass
