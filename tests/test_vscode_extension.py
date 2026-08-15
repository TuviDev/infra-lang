"""Verify the VS Code extension manifest, grammar, snippets and config."""

from __future__ import annotations

import json
from pathlib import Path

EXT = Path("vscode-infra-lang")


class TestExtensionStructure:
    def test_package_json_exists(self):
        assert (EXT / "package.json").exists()

    def test_package_json_valid_json(self):
        data = json.loads((EXT / "package.json").read_text())
        assert "name" in data
        assert "contributes" in data

    def test_language_id_is_infra(self):
        data = json.loads((EXT / "package.json").read_text())
        langs = data["contributes"]["languages"]
        assert any(lang["id"] == "infra" for lang in langs)

    def test_file_extension_is_dot_infra(self):
        data = json.loads((EXT / "package.json").read_text())
        langs = data["contributes"]["languages"]
        infra_lang = next(lang for lang in langs if lang["id"] == "infra")
        assert ".infra" in infra_lang["extensions"]

    def test_tmlanguage_json_exists(self):
        files = list(EXT.glob("syntaxes/*.tmLanguage.json"))
        assert len(files) >= 1

    def test_tmlanguage_valid_json_with_required_fields(self):
        f = next(EXT.glob("syntaxes/*.tmLanguage.json"))
        data = json.loads(f.read_text())
        assert "scopeName" in data
        assert "patterns" in data
        assert data["scopeName"] == "source.infra"

    def test_tmlanguage_covers_keywords(self):
        content = next(EXT.glob("syntaxes/*.tmLanguage.json")).read_text()
        for kw in ["service", "database", "pipeline"]:
            assert kw in content

    def test_tmlanguage_covers_decorators(self):
        content = next(EXT.glob("syntaxes/*.tmLanguage.json")).read_text()
        assert "@" in content

    def test_tmlanguage_covers_builtin_types(self):
        content = next(EXT.glob("syntaxes/*.tmLanguage.json")).read_text()
        for t in ["postgres", "redis", "kafka"]:
            assert t in content

    def test_snippets_json_exists(self):
        files = list(EXT.glob("snippets/*.json"))
        assert len(files) >= 1

    def test_snippets_has_at_least_12(self):
        f = next(EXT.glob("snippets/*.json"))
        data = json.loads(f.read_text())
        assert len(data) >= 12

    def test_each_snippet_has_prefix_and_body(self):
        f = next(EXT.glob("snippets/*.json"))
        for name, snippet in json.loads(f.read_text()).items():
            assert "prefix" in snippet, f"{name!r} missing prefix"
            assert "body" in snippet, f"{name!r} missing body"

    def test_language_config_exists(self):
        assert (EXT / "language-configuration.json").exists()

    def test_language_config_has_comments(self):
        data = json.loads((EXT / "language-configuration.json").read_text())
        assert "comments" in data
        assert data["comments"]["lineComment"] == "#"

    def test_language_config_has_brackets(self):
        data = json.loads((EXT / "language-configuration.json").read_text())
        assert "brackets" in data

    def test_readme_exists_and_is_substantial(self):
        readme = EXT / "README.md"
        assert readme.exists()
        assert len(readme.read_text()) > 100

    def test_extension_js_compiled(self):
        js = Path("vscode-infra-lang/out/extension.js")
        assert js.exists(), (
            "extension.ts not compiled. Run: "
            "cd vscode-infra-lang && npm install && npm run compile"
        )

    def test_extension_has_activatable_main(self):
        import json

        data = json.loads((EXT / "package.json").read_text())
        main = data.get("main", "")
        main_path = Path("vscode-infra-lang") / main
        assert main_path.exists(), f"Extension main not found: {main_path}"

    def test_extension_main_points_to_compiled_js(self):
        import json

        data = json.loads((EXT / "package.json").read_text())
        assert data.get("main") == "./out/extension.js"
        assert (EXT / "out" / "extension.js").exists()
