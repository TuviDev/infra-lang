"""Example files must parse, validate and compile."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infra import parse, validate
from infra.backends.kubernetes import KubernetesBackend

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def get_example_files() -> list:
    if not EXAMPLES_DIR.exists():
        return []
    return sorted(EXAMPLES_DIR.glob("*.infra"))


@pytest.mark.parametrize("example_file", get_example_files(), ids=lambda f: f.name)
def test_example_parses(example_file):
    program = parse(
        example_file.read_text(encoding="utf-8"), filename=str(example_file)
    )
    assert program is not None
    assert len(program.statements) > 0


@pytest.mark.parametrize("example_file", get_example_files(), ids=lambda f: f.name)
def test_example_validates(example_file):
    program = parse(
        example_file.read_text(encoding="utf-8"), filename=str(example_file)
    )
    result = validate(program)
    assert len(result.errors) == 0, [e.message for e in result.errors]


@pytest.mark.parametrize(
    "example_file",
    [f for f in get_example_files() if "pipeline" not in f.name],
    ids=lambda f: f.name,
)
def test_example_compiles_kubernetes(example_file):
    program = parse(
        example_file.read_text(encoding="utf-8"), filename=str(example_file)
    )
    result = KubernetesBackend().compile(program)
    assert len(result.files) > 0
    for fname, content in result.files.items():
        for doc in yaml.safe_load_all(content):
            assert doc is None or isinstance(doc, dict), f"Bad YAML in {fname}"
