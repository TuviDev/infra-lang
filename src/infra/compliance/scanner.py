"""Compliance scanner — evaluates a .infra program against SOC 2 / CIS.

The scanner combines two signal sources:

1. **SEC*/REL* findings** from the existing analyzer checkers
   (:class:`~infra.analyzer.security.SecurityChecker` and
   :class:`~infra.analyzer.reliability.ReliabilityChecker`), mapped to
   controls via :mod:`infra.compliance.mappings`.
2. **Direct AST criteria** for controls that have no SEC/REL code yet
   (CIS 5.2.4 read-only root filesystem, CIS 5.7.3 NetworkPolicy for
   public services).

The result is an audit-readable :class:`ComplianceReport`: every control
is listed as passed or violated, each violation carries the norm ID, the
triggering error code, the file location and a fix recommendation, and
the **Compliance Score** is ``(passed / total) * 100``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from infra.analyzer.reliability import ReliabilityChecker
from infra.analyzer.security import SecurityChecker
from infra.compliance.mappings import (
    DETECTOR_FINDINGS,
    DETECTOR_NETWORK_POLICY_PUBLIC,
    DETECTOR_READ_ONLY_ROOT_FS,
    Control,
    controls_for,
)
from infra.errors.exceptions import InfraError
from infra.parser import ast_nodes as n


class UnknownDetectorError(InfraError, ValueError):
    """Internal wiring error: a control references an unknown detector.

    Inherits :class:`ValueError` for backwards compatibility; it is also an
    :class:`InfraError`, keeping every module error in one hierarchy.
    """


@dataclass(frozen=True)
class ComplianceViolation:
    """One concrete breach of a control, tied to a location in the file."""

    control_id: str  #: norm ID, e.g. ``"CC7.2"`` or ``"5.2.4"``
    code: str  #: triggering error code (SEC*/REL*) or the norm ID itself
    message: str
    location: str  #: ``"file:line:column"`` or ``"unknown"``
    resource: Optional[str]  #: affected service/database, when known
    recommendation: str  #: fix recommendation (finding hint or control fix)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "control_id": self.control_id,
            "code": self.code,
            "message": self.message,
            "location": self.location,
            "resource": self.resource,
            "recommendation": self.recommendation,
        }


@dataclass(frozen=True)
class ControlResult:
    """Outcome of one control: passed when it has no violations."""

    control: Control
    violations: Tuple[ComplianceViolation, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> Dict[str, Any]:
        return {
            "standard": self.control.standard,
            "control_id": self.control.control_id,
            "title": self.control.title,
            "codes": list(self.control.codes),
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
        }


@dataclass(frozen=True)
class ComplianceReport:
    """Full compliance evaluation of one file against one standard set."""

    file: str
    standard: str
    results: Tuple[ControlResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def score(self) -> float:
        """Compliance Score = (passed controls / total controls) * 100."""
        if not self.results:
            return 100.0
        return round((self.passed / self.total) * 100.0, 1)

    @property
    def violations(self) -> Tuple[ComplianceViolation, ...]:
        return tuple(v for r in self.results for v in r.violations)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "standard": self.standard,
            "score": self.score,
            "controls_total": self.total,
            "controls_passed": self.passed,
            "controls_failed": self.failed,
            "results": [r.to_dict() for r in self.results],
        }


# --------------------------------------------------------------------------- #
# Violation builders
# --------------------------------------------------------------------------- #


def _location_str(location: Any) -> str:
    if location is None:
        return "unknown"
    return f"{location.file}:{location.line}:{location.column}"


def _finding_violation(control: Control, finding: Any) -> ComplianceViolation:
    """Translate a SEC*/REL* finding into a control violation."""
    hint = getattr(finding, "hint", None) or control.recommendation
    return ComplianceViolation(
        control_id=control.control_id,
        code=str(getattr(finding, "code", "?")),
        message=str(getattr(finding, "message", "")),
        location=_location_str(getattr(finding, "location", None)),
        resource=None,
        recommendation=str(hint),
    )


def _direct_violation(
    control: Control, service: n.ServiceDef, message: str
) -> ComplianceViolation:
    return ComplianceViolation(
        control_id=control.control_id,
        code=control.control_id,
        message=message,
        location=_location_str(service.location),
        resource=service.name,
        recommendation=control.recommendation,
    )


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #


def _services(program: n.Program) -> List[n.ServiceDef]:
    return [s for s in program.statements if isinstance(s, n.ServiceDef)]


def _collect_findings(program: n.Program) -> List[Any]:
    findings: List[Any] = []
    findings.extend(SecurityChecker().check(program))
    findings.extend(ReliabilityChecker().check(program))
    return findings


def _evaluate_control(
    control: Control, findings: List[Any], services: List[n.ServiceDef]
) -> ControlResult:
    violations: List[ComplianceViolation] = []
    if control.detector == DETECTOR_READ_ONLY_ROOT_FS:
        for svc in services:
            if svc.security is None or not svc.security.read_only_root_filesystem:
                violations.append(
                    _direct_violation(
                        control,
                        svc,
                        f"Service '{svc.name}' does not run with a read-only "
                        "root filesystem.",
                    )
                )
    elif control.detector == DETECTOR_NETWORK_POLICY_PUBLIC:
        for svc in services:
            if svc.expose and svc.network_policy is None:
                violations.append(
                    _direct_violation(
                        control,
                        svc,
                        f"Public service '{svc.name}' (expose: true) has no "
                        "network_policy block.",
                    )
                )
    elif control.detector == DETECTOR_FINDINGS:
        for finding in findings:
            if getattr(finding, "code", None) in control.codes:
                violations.append(_finding_violation(control, finding))
    else:
        raise UnknownDetectorError(
            message=f"Unknown detector {control.detector!r} for "
            f"{control.control_id}"
        )
    return ControlResult(control=control, violations=tuple(violations))


def scan_program(
    program: n.Program, file_name: str, standard: str = "all"
) -> ComplianceReport:
    """Evaluate *program* against every control of *standard*."""
    findings = _collect_findings(program)
    services = _services(program)
    results = tuple(
        _evaluate_control(control, findings, services)
        for control in controls_for(standard)
    )
    return ComplianceReport(file=file_name, standard=standard, results=results)


def scan_file(path: Path, standard: str = "all") -> ComplianceReport:
    """Parse *path* and scan it. Raises on unreadable/unparseable files."""
    from infra.parser import parse_file

    program = parse_file(path)
    return scan_program(program, str(path), standard)
