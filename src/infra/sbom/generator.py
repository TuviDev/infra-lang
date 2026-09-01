"""Deterministic SBOM generator (SPDX 2.3 / CycloneDX 1.5 / markdown / text).

Components are collected from every deployable block in an .infra program:

* ``service``  — its container ``image`` (source-built services without an
  image are skipped: there is no registry artifact to list),
* ``database`` / ``cache`` / ``queue`` — the managed engine rendered as the
  conventional official image reference ``<type>:<version or "latest">``,
* ``storage`` — a managed-service pseudo-component (kind ``managed``);
  there is no container image, so the risk class is always ``ZERO``.

Tag mutability risk classes (spec-driven, three levels only):

* ``ZERO`` — pinned by digest (``@sha256:...``),
* ``HIGH`` — mutable tag (see ``infra.analyzer.security.MUTABLE_TAGS``),
* ``LOW``  — any other tag (semver or otherwise pinned).

Transitive base images come from the bundled ``base_images.json``
(best-effort mapping, e.g. ``nginx`` -> ``alpine``). The optional registry
availability check uses an injectable fetcher so tests never need network.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import infra.parser.ast_nodes as n
from infra.analyzer.security import MUTABLE_TAGS
from infra.version import __version__

FORMATS = ("spdx-json", "cyclonedx-json", "markdown", "text")

RISK_ZERO = "ZERO"
RISK_LOW = "LOW"
RISK_HIGH = "HIGH"
RISK_ORDER = (RISK_HIGH, RISK_LOW, RISK_ZERO)
RISK_BADGES = {RISK_ZERO: "[OK]", RISK_LOW: "[~]", RISK_HIGH: "[!]"}

KIND_CONTAINER = "container"
KIND_MANAGED = "managed"

_REGISTRY_CHECKABLE = re.compile(r"^[a-z0-9.-]+(?::[0-9]+)?$")
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9.-]+")

#: Fetcher signature for the registry availability check. Receives a URL,
#: returns True (found), False (definitively missing) or None (unknown /
#: unreachable / unsupported). Injectable for offline tests.
Fetcher = Callable[[str], Optional[bool]]


@dataclass(frozen=True)
class ImageRef:
    """A parsed container image reference."""

    registry: str
    name: str
    tag: str
    digest: Optional[str] = None


@dataclass(frozen=True)
class SbomComponent:
    """One SBOM entry (deduplicated by image reference)."""

    image: str
    name: str
    version: str
    registry: str
    risk: str
    kind: str = KIND_CONTAINER
    sources: Tuple[str, ...] = ()
    digest: Optional[str] = None
    transitive: bool = False
    #: Parent image references for transitive (base image) entries.
    origins: Tuple[str, ...] = ()
    extra: Tuple[Tuple[str, str], ...] = field(default=())


def parse_image_ref(image: str) -> ImageRef:
    """Split ``registry/name:tag@digest`` into parts (docker.io default)."""
    digest: Optional[str] = None
    base = image
    if "@" in base:
        base, digest = base.split("@", 1)
    parts = base.split("/")
    registry = "docker.io"
    if len(parts) > 1 and (
        "." in parts[0] or ":" in parts[0] or parts[0] == "localhost"
    ):
        registry = parts[0]
        path = "/".join(parts[1:])
    else:
        path = base
    if ":" in path:
        name, tag = path.rsplit(":", 1)
    else:
        name, tag = path, "latest"
    return ImageRef(registry=registry, name=name, tag=tag, digest=digest)


def _classify_risk(ref: ImageRef) -> str:
    if ref.digest:
        return RISK_ZERO
    if ref.tag in MUTABLE_TAGS:
        return RISK_HIGH
    return RISK_LOW


def _basename(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def _merge(into: List[SbomComponent], comp: SbomComponent) -> None:
    """Append *comp*, merging sources when the image is already listed."""
    for idx, existing in enumerate(into):
        if existing.image == comp.image and (
            existing.transitive == comp.transitive
        ):
            merged_sources = tuple(
                dict.fromkeys([*existing.sources, *comp.sources])
            )
            merged_origins = tuple(
                dict.fromkeys([*existing.origins, *comp.origins])
            )
            into[idx] = replace(
                existing, sources=merged_sources, origins=merged_origins
            )
            return
    into.append(comp)


def _service_components(stmt: n.ServiceDef) -> List[SbomComponent]:
    if not stmt.image:
        return []  # source-built service: no registry artifact to list
    ref = parse_image_ref(stmt.image)
    return [
        SbomComponent(
            image=stmt.image,
            name=ref.name,
            version=ref.digest or ref.tag,
            registry=ref.registry,
            risk=_classify_risk(ref),
            sources=(f"service {stmt.name}",),
            digest=ref.digest,
        )
    ]


def _engine_component(kind: str, stmt_name: str, eng_type: str,
                      version: Optional[str]) -> SbomComponent:
    image = f"{eng_type}:{version or 'latest'}"
    ref = parse_image_ref(image)
    return SbomComponent(
        image=image,
        name=ref.name,
        version=ref.tag,
        registry=ref.registry,
        risk=_classify_risk(ref),
        sources=(f"{kind} {stmt_name}",),
    )


def collect_components(program: n.Program) -> List[SbomComponent]:
    """Collect one component per deployable block (dedup by image)."""
    components: List[SbomComponent] = []
    for stmt in program.statements:
        found: List[SbomComponent]
        if isinstance(stmt, n.ServiceDef):
            found = _service_components(stmt)
        elif isinstance(stmt, n.DatabaseDef):
            found = [_engine_component("database", stmt.name, stmt.type,
                                       stmt.version)]
        elif isinstance(stmt, n.CacheDef):
            found = [_engine_component("cache", stmt.name, stmt.type,
                                       stmt.version)]
        elif isinstance(stmt, n.QueueDef):
            found = [_engine_component("queue", stmt.name, stmt.type,
                                       stmt.version)]
        elif isinstance(stmt, n.StorageDef):
            found = [
                SbomComponent(
                    image=f"managed/{stmt.type}",
                    name=stmt.type,
                    version="-",
                    registry="managed",
                    risk=RISK_ZERO,
                    kind=KIND_MANAGED,
                    sources=(f"storage {stmt.name}",),
                )
            ]
        else:
            found = []
        for comp in found:
            _merge(components, comp)
    return components


def load_base_images(path: Optional[Path] = None) -> Dict[str, str]:
    """Load the bundled best-effort base-image database."""
    db_path = path or Path(__file__).with_name("base_images.json")
    data = json.loads(db_path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in data.items()}


def add_transitive(
    components: Sequence[SbomComponent],
    base_images: Optional[Dict[str, str]] = None,
) -> List[SbomComponent]:
    """Append transitive base-image entries (best-effort, deduplicated)."""
    mapping = base_images if base_images is not None else load_base_images()
    result = list(components)
    for comp in components:
        if comp.kind != KIND_CONTAINER or comp.transitive:
            continue
        base_ref = mapping.get(_basename(comp.name))
        if not base_ref:
            continue
        ref = parse_image_ref(base_ref)
        _merge(
            result,
            SbomComponent(
                image=base_ref,
                name=ref.name,
                version=ref.tag,
                registry=ref.registry,
                risk=_classify_risk(ref),
                sources=(f"base of {comp.image}",),
                transitive=True,
                origins=(comp.image,),
            ),
        )
    return result


def component_purl(comp: SbomComponent) -> str:
    """A deterministic package-url for the component."""
    if comp.kind == KIND_MANAGED:
        return f"pkg:generic/{comp.name}"
    name = comp.name.lower()
    purl = f"pkg:docker/{name}@{comp.version}"
    if comp.registry != "docker.io":
        purl += f"?repository_url={comp.registry}"
    return purl


def registry_url(comp: SbomComponent) -> Optional[str]:
    """Best-effort HTTP endpoint used by the registry availability check."""
    if comp.kind != KIND_CONTAINER or not _REGISTRY_CHECKABLE.match(
        comp.registry
    ):
        return None
    if comp.registry == "docker.io":
        if comp.digest:
            return None  # Hub's public tag API cannot resolve digests
        repo = comp.name if "/" in comp.name else f"library/{comp.name}"
        return f"https://hub.docker.com/v2/repositories/{repo}/tags/{comp.version}"
    reference = comp.digest or comp.version
    return (
        f"https://{comp.registry}/v2/{comp.name}/manifests/{reference}"
    )


def _default_fetcher(url: str) -> Optional[bool]:
    import urllib.request

    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=3):
            return True
    except Exception as exc:  # noqa: BLE001 - offline best-effort probe
        status = getattr(exc, "code", None)
        if status == 404:
            return False
        return None


def check_availability(
    components: Sequence[SbomComponent],
    fetcher: Optional[Fetcher] = None,
) -> Dict[str, str]:
    """Map image -> 'found' | 'missing' | 'unknown' (never raises)."""
    probe = fetcher if fetcher is not None else _default_fetcher
    status: Dict[str, str] = {}
    for comp in components:
        url = registry_url(comp)
        if url is None:
            status[comp.image] = "unknown"
            continue
        try:
            answer = probe(url)
        except Exception:  # noqa: BLE001 - defensive: hostile fetcher
            answer = None
        status[comp.image] = (
            "found" if answer is True else "missing" if answer is False
            else "unknown"
        )
    return status


def risk_summary(components: Sequence[SbomComponent]) -> Dict[str, int]:
    counts = {RISK_HIGH: 0, RISK_LOW: 0, RISK_ZERO: 0}
    for comp in components:
        counts[comp.risk] = counts.get(comp.risk, 0) + 1
    return counts


def _spdx_id(comp: SbomComponent) -> str:
    slug = _SLUG_UNSAFE.sub("-", _basename(comp.name)) or "component"
    digest = hashlib.sha1(comp.image.encode("utf-8")).hexdigest()[:8]
    suffix = "-transitive" if comp.transitive else ""
    return f"SPDXRef-Package-{slug}-{digest}{suffix}"


def to_spdx(
    components: Sequence[SbomComponent],
    *,
    project: str,
    checksum: str,
    timestamp: str,
) -> Dict[str, object]:
    """Render an SPDX 2.3 JSON document (as a plain dict)."""
    packages: List[Dict[str, object]] = []
    relationships: List[Dict[str, str]] = []
    for comp in components:
        pkg: Dict[str, object] = {
            "SPDXID": _spdx_id(comp),
            "name": comp.name,
            "versionInfo": comp.version,
            "supplier": "NOASSERTION",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": component_purl(comp),
                }
            ],
            "comment": (
                f"image: {comp.image}; sources: {', '.join(comp.sources)}; "
                f"tag-risk: {comp.risk}"
            ),
        }
        packages.append(pkg)
        relationships.append(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": _spdx_id(comp),
            }
        )
        for origin_image in comp.origins:
            origin = next(
                (c for c in components if c.image == origin_image), None
            )
            if origin is not None:
                relationships.append(
                    {
                        "spdxElementId": _spdx_id(origin),
                        "relationshipType": "DEPENDS_ON",
                        "relatedSpdxElement": _spdx_id(comp),
                    }
                )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{project}-sbom",
        "documentNamespace": (
            "https://spdx.org/spdxdocs/"
            f"infra-lang-{project}-{checksum[:12]}"
        ),
        "creationInfo": {
            "created": timestamp,
            "creators": [f"Tool: infra-lang-{__version__}"],
        },
        "packages": packages,
        "relationships": relationships,
    }


def to_cyclonedx(
    components: Sequence[SbomComponent],
    *,
    project: str,
    checksum: str,
    timestamp: str,
) -> Dict[str, object]:
    """Render a CycloneDX 1.5 JSON document (as a plain dict)."""
    serial = uuid.uuid5(
        uuid.NAMESPACE_URL, f"infra-lang-sbom:{project}:{checksum}"
    )
    cd_components: List[Dict[str, object]] = []
    for comp in components:
        purl = component_purl(comp)
        cd_components.append(
            {
                "type": (
                    KIND_CONTAINER
                    if comp.kind == KIND_CONTAINER
                    else "platform"
                ),
                "bom-ref": purl,
                "name": comp.name,
                "version": comp.version,
                "purl": purl,
                "scope": "required",
                "properties": [
                    {"name": "infra:source", "value": ", ".join(comp.sources)},
                    {"name": "infra:tag-risk", "value": comp.risk},
                    {
                        "name": "infra:transitive",
                        "value": "true" if comp.transitive else "false",
                    },
                ],
            }
        )
    dependencies: List[Dict[str, object]] = []
    for comp in components:
        for origin_image in comp.origins:
            origin = next(
                (c for c in components if c.image == origin_image), None
            )
            if origin is not None:
                dependencies.append(
                    {
                        "ref": component_purl(origin),
                        "dependsOn": [component_purl(comp)],
                    }
                )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": [
                {"vendor": "infra-lang", "name": "infra",
                 "version": __version__}
            ],
            "component": {
                "type": "application",
                "bom-ref": f"pkg:generic/{project}",
                "name": project,
            },
        },
        "components": cd_components,
        "dependencies": dependencies,
    }


def _badge(risk: str) -> str:
    return f"{RISK_BADGES.get(risk, '[?]')} {risk}"


def to_markdown(
    components: Sequence[SbomComponent],
    *,
    project: str,
    source_name: str,
    timestamp: str,
    availability: Optional[Dict[str, str]] = None,
) -> str:
    counts = risk_summary(components)
    lines = [
        f"# Software Bill of Materials: {project}",
        "",
        f"- Source: `{source_name}`",
        f"- Generated: {timestamp}",
        f"- Tool: infra-lang {__version__}",
        f"- Components: {len(components)}",
        (
            "- Risk: "
            f"{RISK_BADGES[RISK_HIGH]} {counts.get(RISK_HIGH, 0)} high, "
            f"{RISK_BADGES[RISK_LOW]} {counts.get(RISK_LOW, 0)} low, "
            f"{RISK_BADGES[RISK_ZERO]} {counts.get(RISK_ZERO, 0)} zero"
        ),
        "",
    ]
    header = "| Image | Registry | Version | Risk | Source |"
    divider = "| --- | --- | --- | --- | --- |"
    if availability is not None:
        header = "| Image | Registry | Version | Risk | Registry check | Source |"
        divider = "| --- | --- | --- | --- | --- | --- |"
    lines.extend([header, divider])
    for comp in components:
        row = (
            f"| `{comp.image}` | {comp.registry} | {comp.version} "
            f"| {_badge(comp.risk)} "
        )
        if availability is not None:
            row += f"| {availability.get(comp.image, 'unknown')} "
        row += f"| {', '.join(comp.sources)} |"
        lines.append(row)
    lines.append("")
    return "\n".join(lines)


def to_text(
    components: Sequence[SbomComponent],
    *,
    project: str,
    source_name: str,
    timestamp: str,
    availability: Optional[Dict[str, str]] = None,
) -> str:
    counts = risk_summary(components)
    lines = [
        f"SBOM: {project}",
        f"  source: {source_name}",
        f"  generated: {timestamp}",
        f"  tool: infra-lang {__version__}",
        (
            f"  components: {len(components)} "
            f"(high={counts.get(RISK_HIGH, 0)}, "
            f"low={counts.get(RISK_LOW, 0)}, "
            f"zero={counts.get(RISK_ZERO, 0)})"
        ),
    ]
    for comp in components:
        line = (
            f"  - {comp.image} [{comp.risk}] ({comp.registry})"
            f" <- {', '.join(comp.sources)}"
        )
        if availability is not None:
            line += f" [registry: {availability.get(comp.image, 'unknown')}]"
        lines.append(line)
    return "\n".join(lines)


def render_sbom(
    components: Sequence[SbomComponent],
    output_format: str,
    *,
    project: str,
    source_name: str,
    checksum: str,
    timestamp: str,
    availability: Optional[Dict[str, str]] = None,
) -> str:
    """Dispatch to the requested format renderer (returns text/JSON str)."""
    fmt = output_format.lower()
    if fmt == "spdx-json":
        doc = to_spdx(components, project=project, checksum=checksum,
                      timestamp=timestamp)
        return json.dumps(doc, indent=2) + "\n"
    if fmt == "cyclonedx-json":
        doc = to_cyclonedx(components, project=project, checksum=checksum,
                           timestamp=timestamp)
        return json.dumps(doc, indent=2) + "\n"
    if fmt == "markdown":
        return to_markdown(
            components,
            project=project,
            source_name=source_name,
            timestamp=timestamp,
            availability=availability,
        )
    if fmt == "text":
        return to_text(
            components,
            project=project,
            source_name=source_name,
            timestamp=timestamp,
            availability=availability,
        )
    raise ValueError(
        f"Unknown SBOM format '{output_format}'. Valid formats: "
        f"{', '.join(FORMATS)}"
    )


__all__ = [
    "FORMATS",
    "KIND_CONTAINER",
    "KIND_MANAGED",
    "RISK_BADGES",
    "RISK_HIGH",
    "RISK_LOW",
    "RISK_ZERO",
    "Fetcher",
    "ImageRef",
    "SbomComponent",
    "add_transitive",
    "check_availability",
    "collect_components",
    "component_purl",
    "load_base_images",
    "parse_image_ref",
    "registry_url",
    "render_sbom",
    "risk_summary",
    "to_cyclonedx",
    "to_markdown",
    "to_spdx",
    "to_text",
]
