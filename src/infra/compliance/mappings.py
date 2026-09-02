"""Mappings from infra-lang SEC*/REL* codes to compliance standards.

Two standards are supported:

**SOC 2 (Trust Services Criteria)**

============  ====================  =========================================
Control       Mapped codes          Theme
============  ====================  =========================================
CC6.1         SEC001, SEC004        Logical access: no hardcoded secrets,
                                    no privileged containers
CC6.3         SEC003                Change management: immutable image tags
CC7.1         SEC003, SEC006        Monitoring: pinned tags, encrypted
                                    database connections
CC7.2         REL004                Incident detection: health checks
A1.1          REL001, REL002,       Availability: startup probes, HA replica
              REL003                counts, memory limits
============  ====================  =========================================

**CIS Kubernetes Benchmark v1.8**

============  ====================  =========================================
Control       Mapped codes          Theme
============  ====================  =========================================
5.1.1         SEC005                Do not run containers as root
5.2.1         SEC004                Minimize privileged containers
5.2.4         *(AST check)*         Read-only root filesystem
5.2.5         SEC004                No privilege escalation (privileged
                                    mode implies escalation is possible)
5.7.3         *(AST check)*         NetworkPolicy for public (exposed)
                                    services
============  ====================  =========================================

Controls marked *(AST check)* have no dedicated SEC/REL code today, so the
scanner evaluates them directly against the parsed service definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

#: Detector kinds: ``findings`` maps SEC/REL codes; the other two are
#: evaluated directly against the AST by the scanner.
DETECTOR_FINDINGS = "findings"
DETECTOR_READ_ONLY_ROOT_FS = "read_only_root_fs"
DETECTOR_NETWORK_POLICY_PUBLIC = "network_policy_public"


@dataclass(frozen=True)
class Control:
    """One auditable criterion of a compliance standard."""

    standard: str  #: ``"soc2"`` or ``"cis"``
    control_id: str  #: e.g. ``"CC6.1"`` or ``"5.2.4"``
    title: str
    #: SEC*/REL* codes whose findings violate this control (may be empty
    #: for AST-evaluated controls).
    codes: Tuple[str, ...] = ()
    #: Control-level fix recommendation used when a finding has no hint.
    recommendation: str = ""
    #: How the scanner evaluates this control.
    detector: str = DETECTOR_FINDINGS


SOC2_CONTROLS: Tuple[Control, ...] = (
    Control(
        standard="soc2",
        control_id="CC6.1",
        title="Logical access — no hardcoded secrets, no privileged access",
        codes=("SEC001", "SEC004"),
        recommendation=(
            "Move secrets to a secret manager (from secret \"...\") and "
            "remove privileged mode from all containers."
        ),
    ),
    Control(
        standard="soc2",
        control_id="CC6.3",
        title="Change management — immutable build artifacts",
        codes=("SEC003",),
        recommendation=(
            "Pin images to immutable version tags or SHA digests instead "
            "of mutable tags like 'latest'."
        ),
    ),
    Control(
        standard="soc2",
        control_id="CC7.1",
        title="System monitoring — configuration baseline and encryption",
        codes=("SEC003", "SEC006"),
        recommendation=(
            "Pin image tags and keep database SSL/TLS enabled so the "
            "deployed configuration stays observable and encrypted."
        ),
    ),
    Control(
        standard="soc2",
        control_id="CC7.2",
        title="Incident detection — health monitoring of services",
        codes=("REL004",),
        recommendation=(
            'Add health checks (health http("/health")) so failures are '
            "detected automatically."
        ),
    ),
    Control(
        standard="soc2",
        control_id="A1.1",
        title="Availability — capacity, fault tolerance and resource limits",
        codes=("REL001", "REL002", "REL003"),
        recommendation=(
            "Add startup probes for large replica sets, use odd replica "
            "counts for HA databases and set memory limits."
        ),
    ),
)

CIS_CONTROLS: Tuple[Control, ...] = (
    Control(
        standard="cis",
        control_id="5.1.1",
        title="Minimize containers running as root (UID 0)",
        codes=("SEC005",),
        recommendation="Run as a non-root user: security { user: 1000 }.",
    ),
    Control(
        standard="cis",
        control_id="5.2.1",
        title="Minimize privileged containers",
        codes=("SEC004",),
        recommendation=(
            "Remove 'privileged: true' from the security block — it grants "
            "full host access."
        ),
    ),
    Control(
        standard="cis",
        control_id="5.2.4",
        title="Containers run with a read-only root filesystem",
        codes=(),
        recommendation=(
            "Add security { read_only_root_filesystem: true } to every "
            "service."
        ),
        detector=DETECTOR_READ_ONLY_ROOT_FS,
    ),
    Control(
        standard="cis",
        control_id="5.2.5",
        title="Privilege escalation must not be allowed",
        codes=("SEC004",),
        recommendation=(
            "Remove 'privileged: true' — privileged mode implies privilege "
            "escalation is possible."
        ),
    ),
    Control(
        standard="cis",
        control_id="5.7.3",
        title="NetworkPolicies applied to public (exposed) services",
        codes=(),
        recommendation=(
            "Add a network_policy { ... } block to every service that is "
            "exposed publicly (expose: true)."
        ),
        detector=DETECTOR_NETWORK_POLICY_PUBLIC,
    ),
)

#: Values accepted by the ``--standard`` CLI option.
STANDARDS = ("soc2", "cis", "all")

#: Human-readable standard names for report headers.
STANDARD_TITLES = {
    "soc2": "SOC 2 (Trust Services Criteria)",
    "cis": "CIS Kubernetes Benchmark v1.8",
    "all": "SOC 2 + CIS Kubernetes Benchmark v1.8",
}


def controls_for(standard: str) -> Tuple[Control, ...]:
    """Return the controls of *standard* (``soc2`` | ``cis`` | ``all``)."""
    if standard == "soc2":
        return SOC2_CONTROLS
    if standard == "cis":
        return CIS_CONTROLS
    if standard == "all":
        return SOC2_CONTROLS + CIS_CONTROLS
    raise ValueError(
        f"Unknown standard '{standard}'. Valid: {list(STANDARDS)}"
    )
