"""Verify the VS Code extension manifest, grammar, snippets and config."""

from __future__ import annotations

import json
from pathlib import Path

EXT = Path("vscode-infra-lang")


class TestExtensionStructure:
    def test_package_json_exists(self):
        assert (EXT / "package.json").exists()

    def test_package_json_valid_json(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        assert "name" in data
        assert "contributes" in data

    def test_language_id_is_infra(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        langs = data["contributes"]["languages"]
        assert any(lang["id"] == "infra" for lang in langs)

    def test_file_extension_is_dot_infra(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        langs = data["contributes"]["languages"]
        infra_lang = next(lang for lang in langs if lang["id"] == "infra")
        assert ".infra" in infra_lang["extensions"]

    def test_tmlanguage_json_exists(self):
        files = list(EXT.glob("syntaxes/*.tmLanguage.json"))
        assert len(files) >= 1

    def test_tmlanguage_valid_json_with_required_fields(self):
        f = next(EXT.glob("syntaxes/*.tmLanguage.json"))
        data = json.loads(f.read_text(encoding="utf-8"))
        assert "scopeName" in data
        assert "patterns" in data
        assert data["scopeName"] == "source.infra"

    def test_tmlanguage_covers_keywords(self):
        content = next(EXT.glob("syntaxes/*.tmLanguage.json")).read_text(
            encoding="utf-8"
        )
        for kw in ["service", "database", "pipeline"]:
            assert kw in content

    def test_tmlanguage_covers_decorators(self):
        content = next(EXT.glob("syntaxes/*.tmLanguage.json")).read_text(
            encoding="utf-8"
        )
        assert "@" in content

    def test_tmlanguage_covers_builtin_types(self):
        content = next(EXT.glob("syntaxes/*.tmLanguage.json")).read_text(
            encoding="utf-8"
        )
        for t in ["postgres", "redis", "kafka"]:
            assert t in content

    def test_snippets_json_exists(self):
        files = list(EXT.glob("snippets/*.json"))
        assert len(files) >= 1

    def test_snippets_has_at_least_12(self):
        f = next(EXT.glob("snippets/*.json"))
        data = json.loads(f.read_text(encoding="utf-8"))
        assert len(data) >= 12

    def test_each_snippet_has_prefix_and_body(self):
        f = next(EXT.glob("snippets/*.json"))
        for name, snippet in json.loads(f.read_text(encoding="utf-8")).items():
            assert "prefix" in snippet, f"{name!r} missing prefix"
            assert "body" in snippet, f"{name!r} missing body"

    def test_language_config_exists(self):
        assert (EXT / "language-configuration.json").exists()

    def test_language_config_has_comments(self):
        data = json.loads(
            (EXT / "language-configuration.json").read_text(encoding="utf-8")
        )
        assert "comments" in data
        assert data["comments"]["lineComment"] == "#"

    def test_language_config_has_brackets(self):
        data = json.loads(
            (EXT / "language-configuration.json").read_text(encoding="utf-8")
        )
        assert "brackets" in data

    def test_readme_exists_and_is_substantial(self):
        readme = EXT / "README.md"
        assert readme.exists()
        assert len(readme.read_text(encoding="utf-8")) > 100

    def test_extension_js_compiled(self):
        js = Path("vscode-infra-lang/out/extension.js")
        assert js.exists(), (
            "extension.ts not compiled. Run: "
            "cd vscode-infra-lang && npm install && npm run compile"
        )

    def test_extension_has_activatable_main(self):
        import json

        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        main = data.get("main", "")
        main_path = Path("vscode-infra-lang") / main
        assert main_path.exists(), f"Extension main not found: {main_path}"

    def test_extension_main_points_to_compiled_js(self):
        import json

        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        assert data.get("main") == "./out/extension.js"
        assert (EXT / "out" / "extension.js").exists()


class TestMarketplaceMetadata:
    """Fields required by the VS Code Marketplace to publish a .vsix."""

    def test_publisher_present(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        assert data.get("publisher")

    def test_license_present(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        assert data.get("license") == "MIT"

    def test_repository_present(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        repo = data.get("repository", {})
        assert repo.get("type") == "git"
        assert "TuviDev/infra-lang" in repo.get("url", "")

    def test_homepage_and_bugs(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        assert "TuviDev.github.io" in data.get("homepage", "")
        assert "TuviDev/infra-lang/issues" in data.get("bugs", {}).get("url", "")

    def test_keywords_and_categories(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        assert "infra" in data.get("keywords", [])
        assert "Programming Languages" in data.get("categories", [])

    def test_icon_exists_and_is_png(self):
        icon = EXT / "icon.png"
        assert icon.exists(), "icon.png missing (Marketplace requires a PNG icon)"
        # PNG magic bytes
        assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_package_script_present(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        assert "package" in data.get("scripts", {})
        assert "vsce" in data["scripts"]["package"]

    def test_vsce_dev_dependency_present(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        deps = data.get("devDependencies", {})
        assert "@vscode/vsce" in deps

    def test_readme_references_marketplace_install(self):
        readme = (EXT / "README.md").read_text(encoding="utf-8")
        assert "Marketplace" in readme
        assert "pip install 'infra-lang[lsp]'" in readme

    def test_extension_workflow_exists(self):
        wf = Path(".github/workflows/extension.yml")
        assert wf.exists(), "extension.yml workflow missing"
        content = wf.read_text(encoding="utf-8")
        assert "vsce package" in content
        assert "upload-artifact" in content


class TestCodeLensConfiguration:
    """Settings contributed for the FinOps CodeLens feature (v0.9.0)."""

    def _props(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        return data["contributes"]["configuration"]["properties"]

    def test_configuration_section_present(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        assert "configuration" in data["contributes"]

    def test_codelens_enabled_key(self):
        props = self._props()
        assert props["infra.codelens.enabled"]["type"] == "boolean"
        assert props["infra.codelens.enabled"]["default"] is True

    def test_codelens_badge_toggles(self):
        props = self._props()
        for key in ("showCost", "showSecurity", "showReliability"):
            cfg = props[f"infra.codelens.{key}"]
            assert cfg["type"] == "boolean"
            assert cfg["default"] is True

    def test_codelens_emoji_enum(self):
        cfg = self._props()["infra.codelens.emoji"]
        assert cfg["type"] == "string"
        assert cfg["enum"] == ["auto", "true", "false"]
        assert cfg["default"] == "auto"

    def test_extension_ts_forwards_settings(self):
        ts = Path("vscode-infra-lang/src/extension.ts").read_text(
            encoding="utf-8"
        )
        assert "initializationOptions" in ts
        assert "infra.codelens.enabled" in ts
        assert "workspace/didChangeConfiguration" in ts


class TestPublishScripts:
    def test_publish_scripts_present(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        scripts = data["scripts"]
        assert "publish:marketplace" in scripts
        assert "publish:openvsx" in scripts
        assert "vsce publish" in scripts["publish:marketplace"]
        assert "ovsx publish" in scripts["publish:openvsx"]

    def test_ovsx_dev_dependency_present(self):
        data = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        assert "ovsx" in data["devDependencies"]

    def test_marketplace_workflow_exists(self):
        wf = Path(".github/workflows/marketplace.yml")
        assert wf.exists(), "marketplace.yml workflow missing"
        content = wf.read_text(encoding="utf-8")
        assert "vsce publish" in content
        assert "ovsx publish" in content
        assert "VSCE_PAT" in content
        assert "OVSX_TOKEN" in content
