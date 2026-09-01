"""Tests for `infra sbom` — generator, formats, transitive bases, CLI.

The SPDX / CycloneDX documents are validated here with a small,
dependency-free structural validator (stdlib only) that pins the subset of
each schema we emit — no external jsonschema library required.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from infra.cli.main import app
from infra.parser import parse
from infra.sbom.generator import (
    FORMATS,
    RISK_HIGH,
    RISK_LOW,
    RISK_ZERO,
    SbomComponent,
    _default_fetcher,
    add_transitive,
    check_availability,
    collect_components,
    component_purl,
    load_base_images,
    parse_image_ref,
    registry_url,
    render_sbom,
    risk_summary,
    to_cyclonedx,
    to_markdown,
    to_spdx,
    to_text,
)

runner = CliRunner()

_DIGEST = "0123456789abcdef" * 4  # realistic 64-char sha256 hex

SOURCE = """\
service web {
  image: "nginx:latest"
  port: 80
}

service edge {
  image: "nginx:latest"
  port: 443
}

service api {
  image: "ghcr.io/acme/api:1.4.2"
  port: 8080
}

service worker {
  image: "acme/worker@sha256:{_DIGEST}"
}

service builder {
  build { context: "./src" }
}

database db {
  type: postgres
  version: "16"
}

cache sessions {
  type: redis
}

queue events {
  type: rabbitmq
  version: "3.13"
}

storage assets {
  type: s3
  size: 100Gi
}
""".replace(
    "@sha256:{_DIGEST}", "@sha256:" + _DIGEST
)


@pytest.fixture()
def components():
    return collect_components(parse(SOURCE))


@pytest.fixture()
def tmp_infra(tmp_path: Path) -> Path:
    file = tmp_path / "stack.infra"
    file.write_text(SOURCE, encoding="utf-8")
    return file


# ---------------------------------------------------------------------------
# stdlib-only structural validators (no external jsonschema dependency)
# ---------------------------------------------------------------------------


_SPDX_ID = re.compile(r"^SPDXRef-[A-Za-z0-9.-]+$")
_ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\+00:00|Z)$")


def _validate_spdx(doc: dict) -> None:
    assert doc["spdxVersion"] == "SPDX-2.3"
    assert doc["dataLicense"] == "CC0-1.0"
    assert doc["SPDXID"] == "SPDXRef-DOCUMENT"
    assert isinstance(doc["name"], str) and doc["name"]
    assert doc["documentNamespace"].startswith("https://spdx.org/spdxdocs/")
    info = doc["creationInfo"]
    assert _ISO_TS.match(info["created"])
    assert any(str(c).startswith("Tool: infra-lang-") for c in
               info["creators"])
    ids = set()
    for pkg in doc["packages"]:
        assert _SPDX_ID.match(pkg["SPDXID"])
        assert pkg["SPDXID"] not in ids, "duplicate SPDXID"
        ids.add(pkg["SPDXID"])
        assert isinstance(pkg["name"], str) and pkg["name"]
        assert isinstance(pkg["versionInfo"], str)
        assert pkg["supplier"] == "NOASSERTION"
        assert pkg["downloadLocation"] == "NOASSERTION"
        assert pkg["filesAnalyzed"] is False
        refs = pkg["externalRefs"]
        assert refs and refs[0]["referenceType"] == "purl"
        assert refs[0]["referenceLocator"].startswith("pkg:")
    valid_rel = {"DESCRIBES", "DEPENDS_ON"}
    for rel in doc["relationships"]:
        assert rel["relationshipType"] in valid_rel
        assert rel["spdxElementId"] in ids | {"SPDXRef-DOCUMENT"}
        assert rel["relatedSpdxElement"] in ids


def _validate_cyclonedx(doc: dict) -> None:
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.5"
    assert doc["version"] == 1
    assert doc["serialNumber"].startswith("urn:uuid:")
    parsed = uuid.UUID(doc["serialNumber"][len("urn:uuid:"):])
    assert parsed.version == 5  # deterministic name-based UUID
    assert _ISO_TS.match(doc["metadata"]["timestamp"])
    tool = doc["metadata"]["tools"][0]
    assert tool["name"] == "infra" and tool["vendor"] == "infra-lang"
    refs = set()
    for comp in doc["components"]:
        assert comp["type"] in {"container", "platform"}
        assert comp["bom-ref"] == comp["purl"]
        assert comp["purl"].startswith("pkg:")
        refs.add(comp["bom-ref"])
        assert isinstance(comp["name"], str) and comp["name"]
        names = {p["name"] for p in comp["properties"]}
        assert {"infra:source", "infra:tag-risk"} <= names
    for dep in doc["dependencies"]:
        assert dep["ref"] in refs
        for target in dep["dependsOn"]:
            assert target in refs


# ---------------------------------------------------------------------------
# image reference parsing & risk classification
# ---------------------------------------------------------------------------


class TestParseImageRef:
    def test_plain_name_defaults(self):
        ref = parse_image_ref("nginx")
        assert (ref.registry, ref.name, ref.tag, ref.digest) == (
            "docker.io",
            "nginx",
            "latest",
            None,
        )

    def test_tag(self):
        ref = parse_image_ref("nginx:1.25.3")
        assert ref.registry == "docker.io"
        assert ref.tag == "1.25.3"

    def test_registry_with_dot(self):
        ref = parse_image_ref("ghcr.io/acme/api:1.4.2")
        assert ref.registry == "ghcr.io"
        assert ref.name == "acme/api"

    def test_registry_with_port(self):
        ref = parse_image_ref("registry.local:5000/app:2")
        assert ref.registry == "registry.local:5000"
        assert ref.name == "app"
        assert ref.tag == "2"

    def test_localhost_registry(self):
        ref = parse_image_ref("localhost/dev/app:latest")
        assert ref.registry == "localhost"

    def test_digest(self):
        img = "acme/worker@sha256:" + "ab" * 32
        ref = parse_image_ref(img)
        assert ref.registry == "docker.io"
        assert ref.name == "acme/worker"
        assert ref.digest == "sha256:" + "ab" * 32


class TestRiskClassification:
    @pytest.mark.parametrize(
        "image",
        ["nginx", "nginx:latest", "app:edge", "app:nightly", "app:dev"],
    )
    def test_high_risk_mutable(self, image):
        comps = collect_components(
            parse(f'service s {{\n  image: "{image}"\n}}\n')
        )
        assert comps[0].risk == RISK_HIGH

    def test_low_risk_pinned_tag(self):
        comps = collect_components(
            parse('service s {\n  image: "nginx:1.25.3"\n}\n')
        )
        assert comps[0].risk == RISK_LOW

    def test_zero_risk_digest(self):
        img = "app@sha256:" + "cd" * 32
        comps = collect_components(
            parse(f'service s {{\n  image: "{img}"\n}}\n')
        )
        assert comps[0].risk == RISK_ZERO
        assert comps[0].version.startswith("sha256:")


# ---------------------------------------------------------------------------
# component collection
# ---------------------------------------------------------------------------


class TestCollectComponents:
    def test_all_block_kinds(self, components):
        sources = {src for c in components for src in c.sources}
        assert "service web" in sources
        assert "database db" in sources
        assert "cache sessions" in sources
        assert "queue events" in sources
        assert "storage assets" in sources

    def test_build_only_service_skipped(self, components):
        assert not any("service builder" in s for c in components
                       for s in c.sources)

    def test_same_image_merges_sources(self, components):
        nginx = next(c for c in components if c.image == "nginx:latest")
        assert nginx.sources == ("service web", "service edge")

    def test_engine_images_synthesized(self, components):
        images = {c.image for c in components}
        assert "postgres:16" in images
        assert "rabbitmq:3.13" in images
        assert "redis:latest" in images  # no version -> latest -> HIGH

    def test_storage_is_managed_zero_risk(self, components):
        storage = next(c for c in components if c.kind == "managed")
        assert storage.risk == RISK_ZERO
        assert storage.version == "-"
        assert storage.registry == "managed"

    def test_cache_without_version_is_high_risk(self, components):
        redis = next(c for c in components if c.image == "redis:latest")
        assert redis.risk == RISK_HIGH

    def test_custom_registry_kept(self, components):
        api = next(c for c in components if "ghcr.io" in c.image)
        assert api.registry == "ghcr.io"
        assert api.risk == RISK_LOW

    def test_risk_summary(self, components):
        counts = risk_summary(components)
        assert counts[RISK_HIGH] == 2  # nginx:latest + redis:latest
        assert counts[RISK_LOW] == 3  # api, postgres:16, rabbitmq:3.13
        assert counts[RISK_ZERO] == 2  # digest worker + managed storage


class TestPurl:
    def test_docker_hub(self, components):
        nginx = next(c for c in components if c.image == "nginx:latest")
        assert component_purl(nginx) == "pkg:docker/nginx@latest"

    def test_custom_registry(self, components):
        api = next(c for c in components if "ghcr.io" in c.image)
        assert component_purl(api).endswith("?repository_url=ghcr.io")

    def test_managed(self, components):
        storage = next(c for c in components if c.kind == "managed")
        assert component_purl(storage) == "pkg:generic/s3"


# ---------------------------------------------------------------------------
# transitive base images
# ---------------------------------------------------------------------------


class TestTransitive:
    def test_base_image_database_ships(self):
        mapping = load_base_images()
        assert len(mapping) >= 45  # ~50 best-effort entries
        assert mapping["nginx"].startswith("alpine")
        assert mapping["postgres"].startswith("debian")

    def test_adds_bases(self, components):
        enriched = add_transitive(components)
        bases = [c for c in enriched if c.transitive]
        assert any(c.image.startswith("alpine") for c in bases)
        assert any(c.image == "debian:bookworm" for c in bases)

    def test_shared_base_merges_origins(self, components):
        enriched = add_transitive(components)
        debian = next(
            c for c in enriched if c.transitive and
            c.image == "debian:bookworm"
        )
        assert set(debian.origins) == {"postgres:16", "redis:latest"}
        assert "base of postgres:16" in debian.sources
        assert "base of redis:latest" in debian.sources

    def test_unknown_image_has_no_base(self):
        comps = collect_components(
            parse('service s {\n  image: "acme/obscure:1"\n}\n')
        )
        assert add_transitive(comps) == comps

    def test_managed_components_get_no_base(self, components):
        enriched = add_transitive(components)
        storage = next(c for c in enriched if c.kind == "managed")
        assert not any(c.origins == (storage.image,) for c in enriched)

    def test_custom_mapping_arg(self, components):
        enriched = add_transitive(components, {"nginx": "scratch"})
        assert any(c.image == "scratch" for c in enriched)


# ---------------------------------------------------------------------------
# registry availability check (fetcher injected; no network)
# ---------------------------------------------------------------------------


class TestRegistryCheck:
    def test_docker_hub_url_uses_library_prefix(self):
        ref = parse_image_ref("nginx:1.25")
        comp = collect_components(
            parse('service s {\n  image: "nginx:1.25"\n}\n')
        )[0]
        assert ref  # parse sanity
        assert registry_url(comp) == (
            "https://hub.docker.com/v2/repositories/library/nginx/tags/1.25"
        )

    def test_docker_hub_org_repo(self):
        comp = collect_components(
            parse('service s {\n  image: "acme/api:2"\n}\n')
        )[0]
        assert "/repositories/acme/api/" in registry_url(comp)

    def test_custom_registry_uses_v2_manifests(self, components):
        api = next(c for c in components if "ghcr.io" in c.image)
        assert registry_url(api) == (
            "https://ghcr.io/v2/acme/api/manifests/1.4.2"
        )

    def test_digest_on_docker_hub_not_checkable(self, components):
        worker = next(c for c in components if c.digest)
        assert registry_url(worker) is None

    def test_digest_on_custom_registry_uses_manifests(self):
        img = "ghcr.io/acme/worker@sha256:" + "ef" * 32
        comp = collect_components(
            parse(f'service s {{\n  image: "{img}"\n}}\n')
        )[0]
        assert registry_url(comp).endswith("/manifests/sha256:" + "ef" * 32)

    def test_managed_not_checkable(self, components):
        storage = next(c for c in components if c.kind == "managed")
        assert registry_url(storage) is None

    def test_availability_statuses(self, components):
        responses = iter([True, False, None])

        def fake_fetcher(url: str):
            return next(responses, None)

        status = check_availability(components, fetcher=fake_fetcher)
        values = list(status.values())
        assert values[0] == "found"
        assert values[1] == "missing"
        assert "unknown" in values  # None answers + managed storage

    def test_hostile_fetcher_never_raises(self, components):
        def hostile(url: str):
            raise RuntimeError("boom")

        status = check_availability(components, fetcher=hostile)
        assert set(status.values()) <= {"found", "missing", "unknown"}
        assert all(value == "unknown" for key, value in status.items()
                   if not key.startswith("managed/"))

    def test_default_fetcher_success(self, monkeypatch):
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda req, timeout=3: _Response()
        )
        assert _default_fetcher("https://example.invalid") is True

    def test_default_fetcher_404(self, monkeypatch):
        def _raise_404(req, timeout=3):
            raise urllib.error.HTTPError(
                req.full_url, 404, "not found", {}, None
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise_404)
        assert _default_fetcher("https://example.invalid") is False

    def test_default_fetcher_network_error_unknown(self, monkeypatch):
        def _raise_oserror(req, timeout=3):
            raise OSError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", _raise_oserror)
        assert _default_fetcher("https://example.invalid") is None


DANGLING = SbomComponent(
    image="alpine:3.20",
    name="alpine",
    version="3.20",
    registry="docker.io",
    risk=RISK_LOW,
    sources=("base of ghost:1",),
    transitive=True,
    origins=("ghost:1",),  # parent not present in the component list
)


# ---------------------------------------------------------------------------
# SPDX / CycloneDX documents
# ---------------------------------------------------------------------------


def _render_json(doc: dict) -> str:
    return json.dumps(doc, indent=2)


class TestSpdx:
    def test_schema_shape(self, components):
        doc = to_spdx(components, project="demo", checksum="abc123",
                      timestamp="2026-09-01T10:00:00+00:00")
        _validate_spdx(json.loads(_render_json(doc)))

    def test_transitive_schema_and_relationships(self, components):
        enriched = add_transitive(components)
        doc = to_spdx(enriched, project="demo", checksum="abc123",
                      timestamp="2026-09-01T10:00:00+00:00")
        _validate_spdx(json.loads(_render_json(doc)))
        dep = [r for r in doc["relationships"]
               if r["relationshipType"] == "DEPENDS_ON"]
        assert len(dep) == 4  # alpine, debian(2 origins), ubuntu

    def test_deterministic(self, components):
        kwargs = dict(project="demo", checksum="abc123",
                      timestamp="2026-09-01T10:00:00+00:00")
        assert to_spdx(components, **kwargs) == to_spdx(components, **kwargs)

    def test_namespace_uses_checksum(self, components):
        doc = to_spdx(components, project="demo", checksum="abc123def456",
                      timestamp="2026-09-01T10:00:00+00:00")
        assert doc["documentNamespace"].endswith("abc123def456"[:12])

    def test_comment_carries_risk(self, components):
        doc = to_spdx(components, project="demo", checksum="x",
                      timestamp="2026-09-01T10:00:00+00:00")
        nginx = next(p for p in doc["packages"] if p["name"] == "nginx")
        assert "tag-risk: HIGH" in nginx["comment"]

    def test_dangling_origin_no_depends_on(self, components):
        doc = to_spdx([*components, DANGLING], project="demo", checksum="x",
                      timestamp="2026-09-01T10:00:00+00:00")
        _validate_spdx(doc)
        dep = [r for r in doc["relationships"]
               if r["relationshipType"] == "DEPENDS_ON"]
        assert dep == []


class TestCycloneDX:
    def test_schema_shape(self, components):
        doc = to_cyclonedx(components, project="demo", checksum="abc123",
                           timestamp="2026-09-01T10:00:00+00:00")
        _validate_cyclonedx(json.loads(_render_json(doc)))

    def test_transitive_dependencies(self, components):
        enriched = add_transitive(components)
        doc = to_cyclonedx(enriched, project="demo", checksum="abc123",
                           timestamp="2026-09-01T10:00:00+00:00")
        _validate_cyclonedx(json.loads(_render_json(doc)))
        assert len(doc["dependencies"]) == 4

    def test_storage_is_platform_type(self, components):
        doc = to_cyclonedx(components, project="demo", checksum="x",
                           timestamp="2026-09-01T10:00:00+00:00")
        storage = next(c for c in doc["components"]
                       if c["purl"] == "pkg:generic/s3")
        assert storage["type"] == "platform"

    def test_serial_number_deterministic(self, components):
        kwargs = dict(project="demo", checksum="abc123",
                      timestamp="2026-09-01T10:00:00+00:00")
        first = to_cyclonedx(components, **kwargs)["serialNumber"]
        second = to_cyclonedx(components, **kwargs)["serialNumber"]
        assert first == second

    def test_dangling_origin_no_dependency_edge(self, components):
        doc = to_cyclonedx([*components, DANGLING], project="demo",
                           checksum="x",
                           timestamp="2026-09-01T10:00:00+00:00")
        _validate_cyclonedx(doc)
        assert doc["dependencies"] == []


# ---------------------------------------------------------------------------
# markdown / text renderers & dispatch
# ---------------------------------------------------------------------------


class TestTextAndMarkdown:
    def test_markdown_table(self, components):
        out = to_markdown(components, project="demo", source_name="demo.infra",
                          timestamp="2026-09-01T10:00:00+00:00")
        assert "| Image | Registry | Version | Risk | Source |" in out
        assert "`nginx:latest`" in out
        assert "[!] HIGH" in out and "[~] LOW" in out and "[OK] ZERO" in out
        assert "2 high" in out

    def test_markdown_availability_column(self, components):
        availability = {c.image: "found" for c in components}
        out = to_markdown(components, project="demo", source_name="demo.infra",
                          timestamp="2026-09-01T10:00:00+00:00",
                          availability=availability)
        assert "| Registry check |" in out
        assert "| found " in out

    def test_text_report(self, components):
        out = to_text(components, project="demo", source_name="demo.infra",
                      timestamp="2026-09-01T10:00:00+00:00")
        assert "SBOM: demo" in out
        assert "nginx:latest [HIGH] (docker.io) <- service web, service edge" \
            in out

    def test_text_availability_suffix(self, components):
        availability = {c.image: "missing" for c in components}
        out = to_text(components, project="demo", source_name="demo.infra",
                      timestamp="2026-09-01T10:00:00+00:00",
                      availability=availability)
        assert "[registry: missing]" in out

    def test_render_all_formats(self, components):
        for fmt in FORMATS:
            out = render_sbom(
                components, fmt, project="demo", source_name="demo.infra",
                checksum="abc", timestamp="2026-09-01T10:00:00+00:00",
            )
            assert isinstance(out, str) and out

    def test_render_unknown_format(self, components):
        with pytest.raises(ValueError, match="Unknown SBOM format"):
            render_sbom(components, "yaml", project="demo",
                        source_name="demo.infra", checksum="abc",
                        timestamp="2026-09-01T10:00:00+00:00")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _invoke(*args: str):
    return runner.invoke(app, ["sbom", *args])


class TestCliSbom:
    def test_default_markdown(self, tmp_infra):
        result = _invoke(str(tmp_infra))
        assert result.exit_code == 0
        assert "# Software Bill of Materials: stack" in result.stdout

    @pytest.mark.parametrize("fmt", FORMATS)
    def test_formats(self, tmp_infra, fmt):
        result = _invoke(str(tmp_infra), "--format", fmt)
        assert result.exit_code == 0

    def test_spdx_json_output_parses(self, tmp_infra):
        result = _invoke(str(tmp_infra), "--format", "spdx-json")
        doc = json.loads(result.stdout)
        assert doc["spdxVersion"] == "SPDX-2.3"

    def test_output_file(self, tmp_infra, tmp_path):
        target = tmp_path / "out" / "sbom.md"
        result = _invoke(str(tmp_infra), "-o", str(target))
        assert result.exit_code == 0
        assert target.read_text(encoding="utf-8").startswith("# Software")

    def test_output_file_text_gets_trailing_newline(self, tmp_infra, tmp_path):
        target = tmp_path / "sbom.txt"
        result = _invoke(str(tmp_infra), "--format", "text", "-o",
                         str(target))
        assert result.exit_code == 0
        content = target.read_text(encoding="utf-8")
        assert content.endswith("\n") and not content.endswith("\n\n")

    def test_missing_file(self, tmp_path):
        result = _invoke(str(tmp_path / "nope.infra"))
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_unknown_format(self, tmp_infra):
        result = _invoke(str(tmp_infra), "--format", "yaml")
        assert result.exit_code == 1
        assert "Valid formats" in result.stdout

    def test_parse_error(self, tmp_path):
        bad = tmp_path / "bad.infra"
        bad.write_text("service {\n", encoding="utf-8")
        result = _invoke(str(bad))
        assert result.exit_code == 1
        assert "Cannot parse" in result.stdout

    def test_include_transitive(self, tmp_infra):
        result = _invoke(str(tmp_infra), "--include-transitive")
        assert result.exit_code == 0
        assert "base of nginx:latest" in result.stdout

    def test_registry_check_is_mockable(self, tmp_infra, monkeypatch):
        captured = {}

        def fake_check(comps, fetcher=None):
            captured["called"] = True
            return {c.image: "found" for c in comps}

        monkeypatch.setattr(
            "infra.cli.sbom_cmd.check_availability", fake_check
        )
        result = _invoke(str(tmp_infra), "--registry-check")
        assert result.exit_code == 0
        assert captured.get("called") is True
        assert "| Registry check |" in result.stdout

    def test_deterministic_output(self, tmp_infra):
        first = _invoke(str(tmp_infra), "--format", "cyclonedx-json").stdout
        second = _invoke(str(tmp_infra), "--format", "cyclonedx-json").stdout
        assert first == second
