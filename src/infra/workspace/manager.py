"""Multi-project workspaces (``infra-workspace.yaml``).

A workspace groups several .infra projects under one YAML manifest so
teams can validate, policy-check and compile them as a unit:

.. code-block:: yaml

    version: "1.0"
    projects:
      api:
        path: services/api.infra
        target: kubernetes
      web:
        path: services/web.infra
        target: compose
    policies:
      - infra-policy.yaml
    environments:
      prod: overlays/prod.infra

**Global policies take precedence per sub-project:** every policy listed at
the workspace level is evaluated for *every* project on
:func:`check_workspace` — a sub-project cannot opt out.  Workspace
**environments** map a name to an .infra overlay file whose
``environment "<name>" { ... }`` blocks are merged onto a project program
when that environment is selected (a project file may still define its own
block of the same name; the workspace overlay is prepended and therefore
wins, mirroring the policies-take-precedence rule).

Everything is local file I/O: the feature is 100% offline and stdlib +
``ruamel.yaml`` only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from infra.errors.exceptions import InfraError

if TYPE_CHECKING:  # pragma: no cover
    from infra.parser import ast_nodes as n
    from infra.policy.engine import PolicyViolation

#: Manifest file name discovered in the working directory.
WORKSPACE_FILE = "infra-workspace.yaml"
#: Manifest schema version this module understands.
WORKSPACE_VERSION = "1.0"
#: Targets accepted for sub-projects (canonical backend names).
TARGETS = ("compose", "kubernetes", "helm", "terraform")
#: Target used when a project entry omits ``target:``.
DEFAULT_TARGET = "kubernetes"
#: Templates accepted by :func:`init_workspace`.
TEMPLATES = ("basic", "micro", "full")


class WorkspaceError(InfraError):
    """Raised for any workspace-level problem (manifest, files, config).

    Part of the unified :class:`infra.errors.exceptions.InfraError`
    hierarchy — the CLI prints ``str(exc)`` which stays human-readable.
    """


@dataclass(frozen=True)
class ProjectSpec:
    """One entry of the manifest ``projects:`` mapping."""

    name: str
    path: str
    target: str = DEFAULT_TARGET


@dataclass(frozen=True)
class Workspace:
    """A parsed and validated ``infra-workspace.yaml``."""

    root: Path
    version: str
    projects: Tuple[ProjectSpec, ...]
    policies: Tuple[str, ...] = ()
    environments: Tuple[Tuple[str, str], ...] = ()

    @property
    def environment_map(self) -> Dict[str, str]:
        return dict(self.environments)

    def project(self, name: str) -> ProjectSpec:
        """Return the named project; raise ``WorkspaceError`` if unknown."""
        for spec in self.projects:
            if spec.name == name:
                return spec
        names = ", ".join(p.name for p in self.projects) or "(none)"
        raise WorkspaceError(f"Unknown project '{name}'. Available: {names}.")

    def project_file(self, spec: ProjectSpec) -> Path:
        return self.root / spec.path

    def policy_files(self) -> List[Path]:
        return [self.root / p for p in self.policies]


@dataclass(frozen=True)
class ProjectReport:
    """Validation + policy outcome for one project."""

    name: str
    path: Path
    target: str
    ok: bool
    errors: Tuple[str, ...] = ()
    violations: Tuple[PolicyViolation, ...] = ()


# --------------------------------------------------------------------------- #
# Manifest loading
# --------------------------------------------------------------------------- #


def load_workspace(path: Path) -> Workspace:
    """Load and validate a workspace manifest; raise ``WorkspaceError``."""
    if not path.is_file():
        raise WorkspaceError(f"Workspace manifest not found: {path}")
    from ruamel.yaml import YAML

    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise WorkspaceError(f"{path}: cannot parse YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceError(f"{path}: expected a mapping at the top level")

    version = data.get("version")
    if str(version) != WORKSPACE_VERSION:
        raise WorkspaceError(
            f"{path}: unsupported version {version!r} "
            f'(expected "{WORKSPACE_VERSION}")'
        )

    raw_projects = data.get("projects")
    if not isinstance(raw_projects, dict) or not raw_projects:
        raise WorkspaceError(f"{path}: 'projects' must be a non-empty mapping")
    projects: List[ProjectSpec] = []
    for name, raw in raw_projects.items():
        where = f"{path}: projects.{name}"
        if not isinstance(name, str):
            raise WorkspaceError(f"{path}: project names must be strings")
        if not isinstance(raw, dict):
            raise WorkspaceError(f"{where} must be a mapping with 'path'")
        rel = raw.get("path")
        if not isinstance(rel, str) or not rel:
            raise WorkspaceError(f"{where}: 'path' must be a non-empty string")
        target = raw.get("target", DEFAULT_TARGET)
        if target not in TARGETS:
            raise WorkspaceError(
                f"{where}: unknown target {target!r}; valid: {list(TARGETS)}"
            )
        projects.append(ProjectSpec(name=name, path=rel, target=target))

    policies_raw = data.get("policies") or []
    if not isinstance(policies_raw, list) or not all(
        isinstance(p, str) for p in policies_raw
    ):
        raise WorkspaceError(
            f"{path}: 'policies' must be a list of policy file paths"
        )

    envs_raw = data.get("environments") or {}
    if not isinstance(envs_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in envs_raw.items()
    ):
        raise WorkspaceError(
            f"{path}: 'environments' must map names to overlay file paths"
        )

    return Workspace(
        root=path.parent,
        version=str(version),
        projects=tuple(projects),
        policies=tuple(policies_raw),
        environments=tuple(sorted(envs_raw.items())),
    )


def find_workspace(directory: Optional[Path] = None) -> Path:
    """Return ``<directory>/infra-workspace.yaml`` or raise ``WorkspaceError``."""
    base = directory or Path.cwd()
    manifest = base / WORKSPACE_FILE
    if not manifest.is_file():
        raise WorkspaceError(
            f"No {WORKSPACE_FILE} found in {base}. "
            "Run 'infra workspace init' to create one."
        )
    return manifest


# --------------------------------------------------------------------------- #
# Program loading & overlays
# --------------------------------------------------------------------------- #


def load_program(
    ws: Workspace, spec: ProjectSpec, environment: Optional[str] = None
) -> n.Program:
    """Parse a project file and apply the *environment* overlay if given.

    Raises ``WorkspaceError`` on a missing/unparseable file, a missing
    workspace overlay file or an unknown environment name.
    """
    from infra.parser import parse_file

    file = ws.project_file(spec)
    if not file.is_file():
        raise WorkspaceError(
            f"Project '{spec.name}': file not found: {file}"
        )
    try:
        program = parse_file(file)
    except Exception as exc:
        detail = str(exc).splitlines()[0] if str(exc) else "parse error"
        raise WorkspaceError(
            f"Project '{spec.name}': cannot parse {file}: {detail}"
        ) from exc
    if environment:
        program = _apply_workspace_overlay(ws, program, environment)
    return program


def _apply_workspace_overlay(
    ws: Workspace, program: n.Program, environment: str
) -> n.Program:
    """Attach workspace overlay blocks (if declared) and apply *environment*."""
    from infra.analyzer.environments import (
        EnvironmentNotFoundError,
        apply_environment_overlay,
    )
    from infra.parser import parse_file

    env_map = ws.environment_map
    if environment in env_map:
        overlay_file = ws.root / env_map[environment]
        if not overlay_file.is_file():
            raise WorkspaceError(
                f"Environment '{environment}': overlay file not found: "
                f"{overlay_file}"
            )
        try:
            overlay_program = parse_file(overlay_file)
        except Exception as exc:
            detail = str(exc).splitlines()[0] if str(exc) else "parse error"
            raise WorkspaceError(
                f"Environment '{environment}': cannot parse {overlay_file}: "
                f"{detail}"
            ) from exc
        specs = tuple(
            e for e in overlay_program.environments if e.name == environment
        )
        if not specs:
            raise WorkspaceError(
                f"Environment '{environment}': {overlay_file} defines no "
                f'environment "{environment}" block'
            )
        program = replace(
            program, environments=specs + program.environments
        )
    try:
        return apply_environment_overlay(program, environment)
    except EnvironmentNotFoundError as exc:
        raise WorkspaceError(str(exc)) from exc


# --------------------------------------------------------------------------- #
# Checking & compiling
# --------------------------------------------------------------------------- #


def check_project(
    ws: Workspace, spec: ProjectSpec, environment: Optional[str] = None
) -> ProjectReport:
    """Validate one project (semantics + every workspace policy).

    Global workspace policies take precedence: they are evaluated for every
    project, and any violation flips the project to not-ok.
    """
    from infra.analyzer.validator import SemanticValidator
    from infra.policy.engine import PolicyError, evaluate_policy, load_policy

    path = ws.project_file(spec)
    errors: List[str] = []
    violations: List[PolicyViolation] = []
    try:
        program = load_program(ws, spec, environment)
    except WorkspaceError as exc:
        return ProjectReport(
            name=spec.name,
            path=path,
            target=spec.target,
            ok=False,
            errors=(str(exc),),
        )

    vresult = SemanticValidator().validate(program)
    for error in vresult.errors:
        loc = error.location
        pos = f"{loc.file}:{loc.line}:{loc.column}" if loc else "?"
        errors.append(f"error[{error.code}] {pos}: {error.message}")

    for policy_file in ws.policy_files():
        try:
            policy = load_policy(policy_file)
        except PolicyError as exc:
            errors.append(str(exc))
            continue
        violations.extend(evaluate_policy(program, policy))

    return ProjectReport(
        name=spec.name,
        path=path,
        target=spec.target,
        ok=not errors and not violations,
        errors=tuple(errors),
        violations=tuple(violations),
    )


def check_workspace(
    ws: Workspace, environment: Optional[str] = None
) -> List[ProjectReport]:
    """Batch-validate every project of the workspace."""
    return [check_project(ws, spec, environment) for spec in ws.projects]


def compile_project(
    ws: Workspace, spec: ProjectSpec, environment: Optional[str] = None
) -> Dict[str, str]:
    """Compile one project with its configured target.

    Raises ``WorkspaceError`` on load/semantic/compile problems.
    """
    from infra.analyzer.validator import SemanticValidator
    from infra.backends import get_backend

    program = load_program(ws, spec, environment)
    vresult = SemanticValidator().validate(program)
    if not vresult.is_valid:
        first = vresult.errors[0]
        raise WorkspaceError(
            f"Project '{spec.name}': {len(vresult.errors)} semantic "
            f"error(s); first: {first.message}"
        )
    try:
        return get_backend(spec.target).compile(program).files
    except Exception as exc:
        raise WorkspaceError(
            f"Project '{spec.name}': compilation failed: {exc}"
        ) from exc


def project_status(ws: Workspace, spec: ProjectSpec) -> str:
    """Quick status for `workspace list`: missing / parse-error / invalid / valid."""
    from infra.analyzer.validator import SemanticValidator
    from infra.parser import parse_file

    file = ws.project_file(spec)
    if not file.is_file():
        return "missing"
    try:
        program = parse_file(file)
    except Exception:
        return "parse-error"
    if SemanticValidator().validate(program).is_valid:
        return "valid"
    return "invalid"


# --------------------------------------------------------------------------- #
# Templates for `workspace init`
# --------------------------------------------------------------------------- #

_APP_INFRA = '''service app {
  image: "nginx:1.27"
  port: 8080
  replicas: 2
  health http("/health") {
    interval: 30s
    timeout: 5s
  }
}
'''

_API_INFRA = '''service api {
  image: "registry.example.com/api:1.0.0"
  port: 8080
  replicas: 2
  health http("/health") {
    interval: 30s
    timeout: 5s
  }
}

database db {
  type: postgres
  version: "16"
  storage: 20Gi
}
'''

_WEB_INFRA = '''service web {
  image: "registry.example.com/web:1.0.0"
  port: 3000
  replicas: 1
}
'''

_WORKER_INFRA = '''service worker {
  image: "registry.example.com/worker:1.0.0"
  replicas: 1
}
'''

_POLICY_YAML = """version: 1
name: workspace-guardrails
rules:
  - id: no-latest-tag
    type: disallow_image_tag
"""

_PROD_OVERLAY = '''environment "prod" {
  service api {
    replicas: 4
  }

  service worker {
    replicas: 2
  }
}
'''

_BASIC_MANIFEST = """version: "1.0"
projects:
  app:
    path: app.infra
    target: kubernetes
policies: []
environments: {}
"""

_MICRO_MANIFEST = """version: "1.0"
projects:
  api:
    path: services/api.infra
    target: kubernetes
  web:
    path: services/web.infra
    target: compose
  worker:
    path: services/worker.infra
    target: kubernetes
policies:
  - infra-policy.yaml
environments: {}
"""

_FULL_MANIFEST = """version: "1.0"
projects:
  api:
    path: services/api.infra
    target: kubernetes
  web:
    path: services/web.infra
    target: compose
  worker:
    path: services/worker.infra
    target: kubernetes
policies:
  - infra-policy.yaml
environments:
  prod: overlays/prod.infra
"""

_BASIC_FILES = {
    WORKSPACE_FILE: _BASIC_MANIFEST,
    "app.infra": _APP_INFRA,
}

_MICRO_FILES = {
    WORKSPACE_FILE: _MICRO_MANIFEST,
    "services/api.infra": _API_INFRA,
    "services/web.infra": _WEB_INFRA,
    "services/worker.infra": _WORKER_INFRA,
    "infra-policy.yaml": _POLICY_YAML,
}

_FULL_FILES = {
    WORKSPACE_FILE: _FULL_MANIFEST,
    "services/api.infra": _API_INFRA,
    "services/web.infra": _WEB_INFRA,
    "services/worker.infra": _WORKER_INFRA,
    "infra-policy.yaml": _POLICY_YAML,
    "overlays/prod.infra": _PROD_OVERLAY,
}

_TEMPLATE_FILES = {
    "basic": _BASIC_FILES,
    "micro": _MICRO_FILES,
    "full": _FULL_FILES,
}


def init_workspace(directory: Path, template: str = "basic") -> List[Path]:
    """Scaffold ``infra-workspace.yaml`` + starter files into *directory*.

    Returns the written files. Raises ``WorkspaceError`` for an unknown
    template or when a manifest already exists.
    """
    if template not in TEMPLATES:
        raise WorkspaceError(
            f"Unknown template '{template}'. Valid: {list(TEMPLATES)}"
        )
    manifest = directory / WORKSPACE_FILE
    if manifest.is_file():
        raise WorkspaceError(f"{WORKSPACE_FILE} already exists in {directory}")
    written: List[Path] = []
    for rel, content in _TEMPLATE_FILES[template].items():
        dest = directory / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content.lstrip("\ufeff"), encoding="utf-8")
        written.append(dest)
    return written
