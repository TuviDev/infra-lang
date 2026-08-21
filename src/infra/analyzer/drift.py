"""On-disk drift detection: compare compiled output against generated files.

The core problem this addresses: users edit the generated manifests (e.g.
``infra-out/infra.yaml``) by hand after compiling, which silently diverges
from the source ``.infra`` file. ``detect_drift`` recompiles the source in
memory and compares each expected output file against what is on disk,
reporting any differences as unified diffs.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

from infra.backends import get_backend
from infra.parser import parse_file


@dataclass
class DriftResult:
    """Result of a drift check against on-disk generated output."""

    has_drift: bool
    #: list of (relative file path, unified diff text) for files that differ.
    modified_files: List[Tuple[str, str]] = field(default_factory=list)
    #: relative paths of expected output files that are missing on disk.
    missing_files: List[str] = field(default_factory=list)
    #: target backend used for the comparison.
    target: str = "kubernetes"

    @property
    def clean(self) -> bool:
        """True when there is no drift (no modified and no missing files)."""
        return not self.has_drift


def _unified_diff(name: str, expected: str, actual: str) -> str:
    """Return a unified diff between *expected* and *actual* for *name*."""
    from_lines = expected.splitlines(keepends=True)
    to_lines = actual.splitlines(keepends=True)
    diff = difflib.unified_diff(
        from_lines,
        to_lines,
        fromfile=f"{name} (compiled)",
        tofile=f"{name} (on disk)",
        lineterm="",
    )
    return "".join(diff)


def detect_drift(
    infra_path: Path,
    out_dir: Path,
    target: str = "kubernetes",
) -> DriftResult:
    """Compile *infra_path* for *target* and compare against *out_dir*.

    Returns a :class:`DriftResult`. Raises on parse errors (propagated from the
    parser) or on an unknown backend (``InfraCompileError``).
    """
    path = Path(infra_path)
    out = Path(out_dir)
    program = parse_file(path)
    backend = get_backend(target)
    compiled = backend.compile(program)

    modified: List[Tuple[str, str]] = []
    missing: List[str] = []

    for name, expected in compiled.files.items():
        dest = out / name
        if not dest.exists():
            missing.append(name)
            continue
        actual = dest.read_text(encoding="utf-8")
        if actual != expected:
            modified.append((name, _unified_diff(name, expected, actual)))

    has_drift = bool(missing) or bool(modified)
    return DriftResult(
        has_drift=has_drift,
        modified_files=modified,
        missing_files=missing,
        target=target,
    )


def render_drift(result: DriftResult) -> str:
    """Return a human-readable summary of a drift check result."""
    if result.clean:
        return "No drift detected. On-disk files match source compilation."
    lines: List[str] = []
    if result.missing_files:
        lines.append("Missing generated files on disk:")
        for name in result.missing_files:
            lines.append(f"  - {name}")
    if result.modified_files:
        lines.append("Files differ from source compilation:")
        for name, diff in result.modified_files:
            lines.append(f"  {name}")
            for dline in diff.splitlines():
                lines.append(f"    {dline}")
    return "\n".join(lines)
