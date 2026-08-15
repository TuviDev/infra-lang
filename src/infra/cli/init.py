"""`infra init` command — scaffold a new project."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

BASIC_FILES = {
    "services/api.infra": """# API service
service api {
    image: "your-image:1.0.0"
    port: 8080
    replicas: 2
    health http("/health")
    resources {
        requests { cpu: 100m, memory: 128Mi }
        limits   { cpu: 500m, memory: 256Mi }
    }
    depends: [db]
}
""",
    "databases/main.infra": """# Primary database
database db {
    type:    postgres
    version: "15.4"
    size:    10Gi
    ssl:     true
    backup {
        enabled:   true
        schedule:  "0 2 * * *"
        retention: 30d
    }
}
""",
    "secrets/main.infra": """# Database credentials (referenced from a secret manager)
secret db-creds {
    url: from env "DATABASE_URL"
}
""",
    "main.infra": 'import "./services/api.infra"\n'
    'import "./databases/main.infra"\n'
    'import "./secrets/main.infra"\n',
}

MICROSERVICES_FILES = {
    "services/api.infra": """# Public API service
service api {
    image:   "myorg/api:1.0.0"
    port:    8080
    replicas: 3
    health http("/health")
    resources {
        requests { cpu: 200m, memory: 256Mi }
        limits   { cpu: 1000m, memory: 512Mi }
    }
    network_policy {
        deny_from: ["*"] allow_from: [frontend]
        allow_egress: [db, session, events]
    }
    depends: [db, session, events]
    affinity { prefer_same: [frontend] }
}
""",
    "services/worker.infra": """# Background worker service
service worker {
    image:   "myorg/worker:1.0.0"
    replicas: 2
    resources {
        requests { cpu: 100m, memory: 128Mi }
        limits   { cpu: 500m, memory: 256Mi }
    }
    network_policy { deny_from: ["*"] allow_from: [api] allow_egress: [db, events] }
    depends: [events, db]
}
""",
    "services/frontend.infra": """# Public web frontend
service frontend {
    image:   "myorg/frontend:1.0.0"
    port:    3000
    replicas: 2
    expose: true
    ingress { host: "app.example.com" }
    health http("/")
    resources {
        requests { cpu: 100m, memory: 128Mi }
        limits   { cpu: 500m, memory: 256Mi }
    }
    network_policy { deny_from: ["*"] allow_egress: [api] }
    depends: [api]
}
""",
    "databases/main.infra": """# Shared primary database
database db {
    type:    postgres
    version: "15.4"
    size:    20Gi
    ssl:     true
    backup {
        enabled:   true
        schedule:  "0 2 * * *"
        retention: 30d
    }
}
""",
    "caches/session.infra": """# Shared session cache
cache session {
    type:        redis
    version:     "7"
    maxmemory:   512Mi
    persistence: true
}
""",
    "queues/events.infra": """# Shared message queue
queue events {
    type:    rabbitmq
    version: "3.13"
    replicas: 3
}
""",
    "secrets/main.infra": """# Shared secrets
secret app-secrets {
    db_url:  from env "DATABASE_URL"
    api_key: from vault "secret/api-key"
}
""",
    "main.infra": 'import "./services/frontend.infra"\n'
    'import "./services/api.infra"\n'
    'import "./services/worker.infra"\n'
    'import "./databases/main.infra"\n'
    'import "./caches/session.infra"\n'
    'import "./queues/events.infra"\n'
    'import "./secrets/main.infra"\n',
}

TEMPLATES = {
    "basic": BASIC_FILES,
    "microservices": MICROSERVICES_FILES,
}


def init(
    project_name: Optional[str] = typer.Argument(None, help="Project name"),
    template: str = typer.Option(
        "basic",
        "--template",
        help="Template: basic, microservices",
    ),
    target: str = typer.Option("kubernetes", "--target", help="Default backend"),
    no_git: bool = typer.Option(False, "--no-git", help="Do not init a git repo"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Accept defaults without prompting"
    ),
) -> None:
    """Create a new Infra project."""
    name = project_name
    if name is None and not yes:
        name = typer.prompt("Project name", default="my-project")
    if name is None:
        name = "my-project"
    if not yes:
        target = typer.prompt("Target platform", default="kubernetes")

    template = template.lower()
    if template not in TEMPLATES:
        typer.echo(
            f"Unknown template '{template}'. "
            f"Choose from: {', '.join(TEMPLATES)}"
        )
        raise typer.Exit(code=1)

    root = Path(name)
    if root.exists():
        typer.echo(f"Directory {root} already exists")
        raise typer.Exit(code=1)
    root.mkdir()

    infra = root / "infra"
    (infra / "environments").mkdir(parents=True)

    # write template files (nested dirs created on demand)
    for rel, content in TEMPLATES[template].items():
        path = infra / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    (infra / "environments" / "dev.infra").write_text(
        f'environment dev {{ namespace: "{name}-dev" labels: {{ env: "dev" }} }}\n'
    )
    (infra / "environments" / "prod.infra").write_text(
        f'environment prod {{ namespace: "{name}-prod" labels: {{ env: "prod" }} }}\n'
    )

    (root / ".infra-config.yaml").write_text(
        f'version: "1"\nname: {name}\ndefault_target: {target}\noutput_dir: ./infra-out\nenvironments: [dev, staging, prod]\n'  # noqa: E501
    )
    (root / ".gitignore").write_text(
        "infra-out/\n*.pyc\n__pycache__/\n.env\n.env.local\n"
    )
    (root / "README.md").write_text(
        f"# {name}\n\nInfrastructure defined with Infra Language.\n\n## Usage\n\n"
        "infra compile infra/main.infra\ninfra validate infra/\ninfra fmt infra/\n"
    )

    if not no_git:
        import subprocess

        try:
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        except Exception:  # pragma: no cover
            pass

    typer.echo(f"✅ Created project {name}/ (template: {template})")
    typer.echo(f"   Run: cd {name} && infra validate infra/")
