"""In-memory Web API — the Infra Lang compiler for WASM/Pyodide (v0.6.0).

This module exposes the full compiler pipeline (parse → validate → backend
render) plus the reporting generators (visual dashboard, environment
comparison, architecture DAG, AST) behind **pure in-memory functions** that
never touch the local disk, a TTY, or a browser. It is the API surface the
static Web Playground (``web/``) calls from inside Pyodide.

WASM guarantees of this module:
  * no ``sys.exit`` / ``SystemExit`` — errors are returned, not raised
    (except where a *str* return type leaves no error channel: HTML/SVG/AST
    generators propagate ``InfraError`` so the JS side can catch it);
  * no ``webbrowser``, ``subprocess``, ``os.system`` or other process
    management — all probes/CLIs stay in their own CLI modules;
  * no local-disk reads — example programs are embedded as constants, so
    ``list_examples()`` works with the wheel alone (``examples/`` is not
    shipped inside the package).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import replace as _dc_replace
from typing import Any, Dict, Optional

from infra.analyzer.cost import estimate_cost
from infra.analyzer.environments import apply_environment_overlay
from infra.analyzer.ui_generator import (
    generate_compare_html,
    generate_dag_svg,
    generate_ui_html,
)
from infra.analyzer.validator import SemanticValidator
from infra.backends import get_backend
from infra.parser import ast_nodes as n
from infra.parser import parse

__all__ = [
    "compile_to_target",
    "generate_ui_report",
    "export_dag_svg",
    "get_ast_json",
    "list_examples",
]

# ---------------------------------------------------------------------- #
# Embedded example programs (kept in sync with examples/0{1,2,3}_*.infra)
# ---------------------------------------------------------------------- #

_HELLO_WORLD = '''\
# Hello world — the simplest possible service: one web container
# with a health check. Compiles to a Kubernetes Deployment + Service.

service hello {
    image: "nginx:1.25.3"
    port 80
    resources {
      requests: {cpu: 50m, memory: 64Mi}
      limits: {cpu: 200m, memory: 128Mi}
    }
    health http("/")
}
'''

_WEB_APP = '''\
# Web app — a service with a database, a cache, and secrets loaded
# from the environment at deploy time.

secret db-creds {
    password: from env "DB_PASSWORD"
    url: from env "DATABASE_URL"
}

database main-db {
    type: postgres
}

cache session {
    type: redis
    maxmemory: 512Mi
}

service api {
    image: "myapp/api:v1.0.0"
    replicas: 2
    port 8080
    env {
      DB_URL: from secret "db-creds".url
      DB_PASS: from secret "db-creds".password
      NODE_ENV: "production"
    }
    depends: [main-db, session]
    resources {
      requests: {cpu: 200m, memory: 256Mi}
      limits: {cpu: 1000m, memory: 512Mi}
    }
    health http("/health")
}
'''

_MICROSERVICES = '''\
# Microservices — three services sharing a database and a message queue,
# wired with depends/service discovery.

secret shared-creds {
    db_url: from env "DATABASE_URL"
}

database users-db {
    type: postgres
}

queue events {
    type: rabbitmq
}

service auth-svc {
    image: "myapp/auth:v2.1.0"
    port 3001
    replicas: 2
    env {
      DB_URL: from secret "shared-creds".db_url
    }
    depends: [users-db]
    health http("/health")
}

service api-svc {
    image: "myapp/api:v2.1.0"
    replicas: 3
    env {
      AUTH_URL: "http://auth-svc:3001"
    }
    depends: [auth-svc, events]
    health http("/health")
}

service worker-svc {
    image: "myapp/worker:v2.1.0"
    replicas: 2
    env {
      QUEUE_URL: "amqp://events:5672"
    }
    depends: [events]
    health http("/health")
}
'''

_EXAMPLES: Dict[str, str] = {
    "hello_world": _HELLO_WORLD,
    "web_app": _WEB_APP,
    "microservices": _MICROSERVICES,
}


def _load_program(source_code: str, env_name: Optional[str]) -> n.Program:
    """Parse *source_code* and optionally apply an environment overlay.

    Same quirk handling as the dashboard: applying the overlay strips
    ``program.environments`` (by design, so backends never re-apply it) —
    the list is restored so downstream generators keep full context.
    Raises ``InfraError`` on parse problems and
    ``EnvironmentNotFoundError`` for unknown overlay names.
    """
    program = parse(source_code, filename="<playground>")
    if not env_name:
        return program
    merged = apply_environment_overlay(program, env_name)
    return _dc_replace(merged, environments=program.environments)


def compile_to_target(
    source_code: str, target: str = "kubernetes", env_name: Optional[str] = None
) -> Dict[str, Any]:
    """Compile *source_code* fully in memory for *target*.

    Returns ``{"success": bool, "files": {filename: content}, "errors": [...]}``.
    A semantic validation failure, parse error, unknown target or unknown
    environment yields ``success=False`` with the human-readable messages —
    this function never raises and never writes to disk.
    """
    try:
        program = _load_program(source_code, env_name)
        result = SemanticValidator().validate(program)
        if not result.is_valid:
            return {
                "success": False,
                "files": {},
                "errors": [
                    f"error[{e.code}] {e.message}" for e in result.errors
                ],
            }
        files = get_backend(target).compile(program).files
        return {"success": True, "files": dict(files), "errors": []}
    except Exception as exc:  # noqa: BLE001 - API contract: errors as data
        message = str(exc) or type(exc).__name__
        return {"success": False, "files": {}, "errors": [message]}


def generate_ui_report(
    source_code: str,
    env_name: Optional[str] = None,
    compare_env: Optional[str] = None,
) -> str:
    """Render the self-contained dashboard (or compare report) HTML.

    With *compare_env*, the two overlays *env_name* (default ``base``) and
    *compare_env* are rendered side by side. Raises ``InfraError`` on parse
    problems and ``EnvironmentNotFoundError`` for unknown overlay names.
    """
    if compare_env is not None:
        base = parse(source_code, filename="<playground>")
        return generate_compare_html(base, env_name or "base", compare_env)
    program = _load_program(source_code, env_name)
    return generate_ui_html(
        program,
        estimate_cost(program),
        drift_report=None,
        env_name=env_name,
    )


def export_dag_svg(source_code: str, env_name: Optional[str] = None) -> str:
    """Render the architecture DAG of *source_code* as a standalone SVG.

    Same collector and longest-path layout as the dashboard and
    `infra graph --format svg`. Raises ``InfraError`` on parse problems and
    ``EnvironmentNotFoundError`` for unknown overlay names.
    """
    return generate_dag_svg(_load_program(source_code, env_name))


def get_ast_json(source_code: str) -> Dict[str, Any]:
    """Return the parsed AST of *source_code* as a plain JSON-safe dict.

    The round-trip through :mod:`json` guarantees the result serializes
    cleanly in every host (tuples become lists, no custom objects survive).
    Raises ``InfraError`` on parse problems.
    """
    program = parse(source_code, filename="<playground>")
    normalized: Dict[str, Any] = json.loads(json.dumps(asdict(program)))
    return normalized


def list_examples() -> Dict[str, str]:
    """Return the embedded example programs ``{name: source}``.

    Kept inside the package (not read from ``examples/`` on disk) so the
    function works inside Pyodide with nothing but the installed wheel.
    """
    return dict(_EXAMPLES)
