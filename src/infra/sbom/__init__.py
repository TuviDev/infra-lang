"""Software Bill of Materials (SBOM) generation for .infra files (v0.9.0).

Deterministic, offline-first: the SBOM is derived purely from the parsed
AST plus a best-effort, bundled base-image database. The optional registry
availability check is injectable so CI never touches the network.
"""

from infra.sbom.generator import (
    FORMATS,
    RISK_BADGES,
    RISK_HIGH,
    RISK_LOW,
    RISK_ZERO,
    SbomComponent,
    add_transitive,
    check_availability,
    collect_components,
    load_base_images,
    parse_image_ref,
    render_sbom,
)

__all__ = [
    "FORMATS",
    "RISK_BADGES",
    "RISK_HIGH",
    "RISK_LOW",
    "RISK_ZERO",
    "SbomComponent",
    "add_transitive",
    "check_availability",
    "collect_components",
    "load_base_images",
    "parse_image_ref",
    "render_sbom",
]
