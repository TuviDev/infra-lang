"""Import resolver tests."""

from __future__ import annotations

import pytest

from infra import parse, validate
from infra.backends.kubernetes import KubernetesBackend
from infra.errors.exceptions import InfraParseError
from infra.parser import parse_file
from infra.resolver.imports import ImportCycleError


class TestImportResolver:
    def test_imported_const_resolves_in_importing_file(self, tmp_path):
        (tmp_path / "base.infra").write_text('const BASE_IMAGE = "alpine:3.18"')
        (tmp_path / "main.infra").write_text(
            'import "./base.infra"\nservice api { image: BASE_IMAGE }'
        )
        program = parse_file(tmp_path / "main.infra")
        result = validate(program)
        assert not any(e.code == "E001" for e in result.errors), \
            [e.message for e in result.errors]

    def test_import_makes_const_available(self, tmp_path):
        (tmp_path / "lib.infra").write_text('const MY_TAG = "v1.0.0"')
        (tmp_path / "main.infra").write_text(
            'import "./lib.infra"\nservice api { image: `nginx:{MY_TAG}` }'
        )
        program = parse_file(tmp_path / "main.infra")
        files = KubernetesBackend().compile(program).files
        content = "\n".join(files.values())
        assert "nginx:v1.0.0" in content

    def test_circular_import_detected(self, tmp_path):
        (tmp_path / "a.infra").write_text('import "./b.infra"')
        (tmp_path / "b.infra").write_text('import "./a.infra"')
        with pytest.raises((ImportCycleError, RecursionError)):
            parse_file(tmp_path / "a.infra")

    def test_nonexistent_import_raises(self, tmp_path):
        (tmp_path / "main.infra").write_text('import "./nonexistent.infra"')
        with pytest.raises((InfraParseError, FileNotFoundError)):
            parse_file(tmp_path / "main.infra")

    def test_nested_imports(self, tmp_path):
        (tmp_path / "c.infra").write_text('const C_VAL = "ccc"')
        (tmp_path / "b.infra").write_text('import "./c.infra"\nconst B_VAL = "bbb"')
        (tmp_path / "main.infra").write_text('import "./b.infra"\nservice s { image: C_VAL }')
        program = parse_file(tmp_path / "main.infra")
        result = validate(program)
        assert not any(e.code == "E001" for e in result.errors)

    def test_from_import_selective(self, tmp_path):
        (tmp_path / "lib.infra").write_text('const ALPHA = "a"\nconst BETA = "b"')
        (tmp_path / "main.infra").write_text(
            'from "./lib.infra" import ALPHA\nservice api { image: ALPHA }'
        )
        program = parse_file(tmp_path / "main.infra")
        result = validate(program)
        assert not any(e.code == "E001" and "ALPHA" in e.message for e in result.errors)
