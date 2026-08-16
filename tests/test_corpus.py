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
    program = parse(f.read_text(encoding="utf-8"), filename=str(f))
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

    program = parse(f.read_text(encoding="utf-8"), filename=str(f))
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
        parse(f.read_text(encoding="utf-8"), filename=str(f))
    except (InfraParseError, InfraLexError):
        pass


def _expected_marker(content: str):
    """Parse the ``# Expected: <token>`` header of an invalid corpus file.

    Returns a tuple ``(kind, token)`` where ``kind`` is ``"code"`` (e.g. E001),
    ``"parse"`` (expect an InfraParseError) or ``"lex"`` (expect an
    InfraLexError). Returns ``None`` if no recognized marker is present.
    """
    for line in content.split("\n"):
        if "Expected:" not in line:
            continue
        val = line.split("Expected:", 1)[1].strip()
        import re

        code = re.search(r"\b([A-Z]+\d{3})\b", val)
        if code:
            return ("code", code.group(1))
        if "InfraParseError" in val or "parse" in val:
            return ("parse", None)
        if "InfraLexError" in val or "lex" in val:
            return ("lex", None)
        return ("unknown", val)
    return None


def _invalid_files():
    return (
        list((CORPUS / "invalid").glob("*.infra"))
        if (CORPUS / "invalid").exists()
        else []
    )


def test_every_invalid_file_declares_expected_marker():
    """Every invalid corpus file must declare what error it expects.

    This guards against an invalid file that only passes because the test
    swallowed its failure without ever asserting the error contract.
    """
    for f in _invalid_files():
        marker = _expected_marker(f.read_text(encoding="utf-8"))
        assert marker is not None, (
            f"{f.name}: invalid corpus file has no '# Expected:' marker"
        )
        assert marker[0] in ("code", "parse", "lex"), (
            f"{f.name}: unrecognized expected marker: {marker}"
        )


@pytest.mark.parametrize(
    "f",
    _invalid_files(),
    ids=lambda f: f.stem,
)
def test_invalid_files_fail_with_known_error(f):
    content = f.read_text(encoding="utf-8")
    expected = _expected_marker(content)

    try:
        program = parse(content, filename=str(f))
    except InfraLexError:
        # A lex error is only acceptable if that's what the file expects.
        assert expected and expected[0] == "lex", (
            f"{f.name}: got InfraLexError but expected {expected}"
        )
        return
    except InfraParseError:
        # A parse error is only acceptable if the file expects a parse error
        # (or no specific code). If it expects a semantic code, fail.
        assert not (expected and expected[0] == "code"), (
            f"{f.name}: expected code {expected[1]} but got InfraParseError"
        )
        assert not (expected and expected[0] == "unknown"), (
            f"{f.name}: expected marker {expected} but got InfraParseError"
        )
        return

    # Parsed OK -> the file must fail semantic validation with the code.
    result = validate(program)
    if expected and expected[0] == "code":
        all_codes = {e.code for e in result.errors}
        assert expected[1] in all_codes, (
            f"{f.name}: expected {expected[1]}, got {all_codes}"
        )
    else:
        assert not result.is_valid
