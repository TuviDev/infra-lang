"""`infra learn` — interactive terminal tutor for the .infra DSL.

A built-in mini-course with five structured lessons. Each lesson has a
goal, a short theory section with a runnable example, a task to solve,
and the expected pattern. Non-interactive flags make CI integration
deterministic:

* ``--list`` prints the lesson table,
* ``--lesson N`` renders one lesson,
* ``--verify N <file.infra>`` checks a solution against the real parser
  and the semantic validator plus lesson-specific structural checks.

Lesson prose is plain ASCII on purpose: output must render identically
on Linux, macOS and Windows consoles (incl. legacy code pages).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import typer

from infra.errors.exceptions import InfraParseError
from infra.parser import _parser
from infra.parser import ast_nodes as n

_RULE = "=" * 72
_THIN = "-" * 72


@dataclass(frozen=True)
class Lesson:
    """One lesson of the built-in .infra mini-course."""

    number: int
    title: str
    goal: str
    theory: str
    example: str
    task: str
    pattern: str


LessonChecks = Callable[[n.Program], List[str]]


# --------------------------------------------------------------------- #
# Lesson content (examples are real, parseable .infra programs)
# --------------------------------------------------------------------- #

_LESSON_1 = Lesson(
    number=1,
    title="First service (Single Service & Image)",
    goal="Declare a runnable container workload.",
    theory=(
        "Everything starts with a 'service' block. A service is a\n"
        "container workload; the only strictly required piece inside is\n"
        "the container 'image:'. Optionally expose a port with 'port N'\n"
        "and scale horizontally with 'replicas: N'."
    ),
    example='service web {\n    image: "nginx:1.25"\n    port: 80\n    replicas: 1\n}',
    task=(
        "Create a file with a single service named `web` that uses the\n"
        "image `nginx:1.25` and exposes port 80."
    ),
    pattern='service web {\n    image: "nginx:1.25"\n    port: 80\n}',
)

_LESSON_2 = Lesson(
    number=2,
    title="Database and secrets (Database, Secrets & Environment)",
    goal="Wire a managed database into a service without leaking values.",
    theory=(
        "A 'database' block provisions a managed store (postgres, mysql,\n"
        "...). Credentials never live in source: a 'secret' block pulls\n"
        "values 'from env \"VAR\"' at deploy time, and a service consumes\n"
        "them with 'from secret \"name\".key' inside its env block."
    ),
    example=(
        "secret db-creds {\n"
        '    password: from env "DB_PASSWORD"\n'
        "}\n"
        "database main-db {\n"
        "    type: postgres\n"
        "}\n"
        "service api {\n"
        '    image: "myapp/api:v1"\n'
        "    env {\n"
        '        DB_PASS: from secret "db-creds".password\n'
        "    }\n"
        "    depends: [main-db]\n"
        "}"
    ),
    task=(
        "Declare a postgres database, a secret sourced from an\n"
        "environment variable, and a service whose env reads that secret."
    ),
    pattern=(
        "secret app-secret {\n"
        '    password: from env "APP_DB_PASSWORD"\n'
        "}\n"
        "database db {\n"
        "    type: postgres\n"
        "}\n"
        "service api {\n"
        '    image: "app:dev"\n'
        "    env {\n"
        '        PASSWORD: from secret "app-secret".password\n'
        "    }\n"
        "    depends: [db]\n"
        "}"
    ),
)

_LESSON_3 = Lesson(
    number=3,
    title="Microservices and dependencies (depends_on & Network)",
    goal="Model several cooperating workloads on a shared network.",
    theory=(
        "Real systems are graphs: queue/cache/database blocks sit next\n"
        "to services, and 'depends' / 'depends_on' declares the startup\n"
        "order and the dependency edges between them. A top-level\n"
        "'network' block gives the whole file an isolated CIDR."
    ),
    example=(
        "network app-net {\n"
        '    cidr: "10.20.0.0/16"\n'
        "}\n"
        "queue events {\n"
        "    type: rabbitmq\n"
        "}\n"
        "service api {\n"
        '    image: "myapp/api:v2"\n'
        "    depends_on: [events]\n"
        "}\n"
        "service worker {\n"
        '    image: "myapp/worker:v2"\n'
        "    depends_on: [events, api]\n"
        "}"
    ),
    task=(
        "Model two or more services connected by depends_on edges and\n"
        "place them on a declared network block."
    ),
    pattern=(
        "network backend {\n"
        '    cidr: "10.30.0.0/16"\n'
        "}\n"
        "service api {\n"
        '    image: "app/api:1"\n'
        "}\n"
        "service worker {\n"
        '    image: "app/worker:1"\n'
        "    depends_on: [api]\n"
        "}"
    ),
)

_LESSON_4 = Lesson(
    number=4,
    title="Reliability and scaling (Health, Autoscale, Disruption)",
    goal="Make a service survive production traffic.",
    theory=(
        "Production services need three guards: 'health http(\"/path\")'\n"
        "so dead replicas are restarted, 'autoscale { min, max,\n"
        "target_cpu }' so replicas follow the load, and 'disruption {\n"
        "max_unavailable }' so voluntary disruptions never drain\n"
        "everything at once."
    ),
    example=(
        "service api {\n"
        '    image: "myapp/api:v3"\n'
        "    replicas: 2\n"
        '    health http("/health")\n'
        "    autoscale {\n"
        "        min: 2\n"
        "        max: 10\n"
        "        target_cpu: 80\n"
        "    }\n"
        "    disruption {\n"
        "        max_unavailable: 1\n"
        "    }\n"
        "}"
    ),
    task=(
        "Harden one service: add an HTTP health check, an autoscale\n"
        "window with a CPU target, and a disruption budget."
    ),
    pattern=(
        "service api {\n"
        '    image: "app/api:3"\n'
        "    replicas: 2\n"
        '    health http("/health")\n'
        "    autoscale {\n"
        "        min: 2\n"
        "        max: 6\n"
        "        target_cpu: 75\n"
        "    }\n"
        "    disruption {\n"
        "        min_available: 1\n"
        "    }\n"
        "}"
    ),
)

_LESSON_5 = Lesson(
    number=5,
    title="Advanced policies (NetworkPolicy, SecretStore, Cron Schedule)",
    goal="Lock down traffic and externalize secret management.",
    theory=(
        "A top-level 'network_policy \"name\"' restricts traffic between\n"
        "workloads (target, allow_ingress/allow_egress lists,\n"
        "block_all_ingress). 'secret_store \"name\"' points secrets at an\n"
        "external provider (vault/aws/gcp/k8s). Inside a service,\n"
        "'schedule' scales replicas by cron expressions per time window."
    ),
    example=(
        'secret_store "vault-prod" {\n'
        '    provider: "vault"\n'
        '    address: "https://vault.internal:8200"\n'
        "}\n"
        'network_policy "api-ingress" {\n'
        "    target: api\n"
        "    allow_ingress: [frontend]\n"
        "    block_all_ingress: false\n"
        "}\n"
        "service frontend {\n"
        '    image: "app/frontend:1"\n'
        "}\n"
        "service api {\n"
        '    image: "app/api:1"\n'
        "    schedule {\n"
        "        default: replicas 2\n"
        '        "0 9 * * 1-5": replicas 6\n'
        "    }\n"
        "}"
    ),
    task=(
        "Secure the api: declare a secret_store, a network_policy that\n"
        "whitelists the frontend, and give api a cron-based schedule."
    ),
    pattern=(
        'secret_store "corp-vault" {\n'
        '    provider: "vault"\n'
        '    address: "https://vault.corp:8200"\n'
        "}\n"
        'network_policy "only-frontend" {\n'
        "    target: api\n"
        "    allow_ingress: [frontend]\n"
        "}\n"
        "service frontend {\n"
        '    image: "app/front:1"\n'
        "}\n"
        "service api {\n"
        '    image: "app/api:1"\n'
        "    schedule {\n"
        "        default: replicas 1\n"
        '        "0 8 * * 1-5": replicas 4\n'
        "    }\n"
        "}"
    ),
)

LESSONS: List[Lesson] = [_LESSON_1, _LESSON_2, _LESSON_3, _LESSON_4, _LESSON_5]


# --------------------------------------------------------------------- #
# Structural, per-lesson expectations (beyond plain semantic validity)
# --------------------------------------------------------------------- #


def _services(program: n.Program) -> List[n.ServiceDef]:
    return [s for s in program.statements if isinstance(s, n.ServiceDef)]


def _failures_l1(program: n.Program) -> List[str]:
    fails: List[str] = []
    svcs = _services(program)
    if not svcs:
        fails.append("at least one 'service' block")
    if any(not s.image for s in svcs):
        fails.append("every service needs an 'image:'")
    return fails


def _failures_l2(program: n.Program) -> List[str]:
    fails = []
    if not any(isinstance(s, n.DatabaseDef) for s in program.statements):
        fails.append("a 'database' block")
    if not any(isinstance(s, n.SecretDef) for s in program.statements):
        fails.append("a 'secret' block sourcing values from env")
    if not any(
        entry.from_secret for svc in _services(program) for entry in svc.env
    ):
        fails.append("a service env entry using 'from secret \"name\".key'")
    return fails


def _failures_l3(program: n.Program) -> List[str]:
    fails = []
    svcs = _services(program)
    if len(svcs) < 2:
        fails.append("at least two 'service' blocks")
    if not any(s.depends or s.depends_on for s in svcs):
        fails.append("at least one 'depends'/'depends_on' edge")
    if not any(isinstance(s, n.NetworkDef) for s in program.statements):
        fails.append("a top-level 'network' block")
    return fails


def _failures_l4(program: n.Program) -> List[str]:
    fails = []
    svcs = _services(program)
    if not any(s.health is not None or s.probes is not None for s in svcs):
        fails.append("a service with a health check (or probes)")
    if not any(s.autoscale is not None for s in svcs):
        fails.append("a service with an 'autoscale' block")
    if not any(s.disruption is not None for s in svcs):
        fails.append("a service with a 'disruption' block")
    return fails


def _failures_l5(program: n.Program) -> List[str]:
    fails = []
    if not any(isinstance(s, n.NetworkPolicyDef) for s in program.statements):
        fails.append("a top-level 'network_policy' block")
    if not any(isinstance(s, n.SecretStoreDef) for s in program.statements):
        fails.append("a 'secret_store' block")
    if not any(s.schedule is not None for s in _services(program)):
        fails.append("a service with a cron 'schedule' block")
    return fails


_CHECKERS: List[LessonChecks] = [
    _failures_l1,
    _failures_l2,
    _failures_l3,
    _failures_l4,
    _failures_l5,
]


# --------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------- #


def _lesson_by_number(number: int) -> Optional[Lesson]:
    for lesson in LESSONS:
        if lesson.number == number:
            return lesson
    return None


def render_lesson_list() -> str:
    """The ``--list`` table: number, title and goal of every lesson."""
    lines = [
        "infra learn - available lessons",
        _THIN,
    ]
    for lesson in LESSONS:
        lines.append(f"  [{lesson.number}] {lesson.title}")
        lines.append(f"      Goal: {lesson.goal}")
    lines.append(_THIN)
    lines.append("Show one:   infra learn --lesson <N>")
    lines.append("Verify:     infra learn solution.infra --verify <N>")
    return "\n".join(lines)


def render_lesson(lesson: Lesson) -> str:
    """Full text of one lesson: goal, theory, example, task, pattern."""
    return "\n".join(
        [
            _RULE,
            f"LESSON {lesson.number}/5: {lesson.title}",
            _RULE,
            f"GOAL\n  {lesson.goal}",
            "",
            "THEORY",
            lesson.theory,
            "",
            "EXAMPLE",
            lesson.example,
            "",
            "YOUR TASK",
            lesson.task,
            "",
            "EXPECTED PATTERN (one valid solution)",
            lesson.pattern,
            "",
            _THIN,
            f"Verify your file:  infra learn your.infra --verify {lesson.number}",
        ]
    )


# --------------------------------------------------------------------- #
# Verification (--verify N + path)
# --------------------------------------------------------------------- #


def verify_solution(number: int, path: Path) -> bool:
    """Check *path* against lesson *number*; print the verdict.

    Returns ``True`` when the file parses, passes the semantic
    validator and satisfies the lesson's structural expectations.
    """
    lesson = _lesson_by_number(number)
    if lesson is None:
        typer.echo(
            f"[FAIL] Unknown lesson {number} (valid: 1-{len(LESSONS)}).",
            err=True,
        )
        raise typer.Exit(code=1)
    if not path.is_file():
        typer.echo(f"[FAIL] File not found: {path}", err=True)
        raise typer.Exit(code=1)

    try:
        program = _parser().parse_file(path)
    except InfraParseError as exc:
        typer.echo(f"[FAIL] Lesson {number}: file does not parse:", err=True)
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    from infra.analyzer.validator import SemanticValidator

    result = SemanticValidator().validate(program)
    problems = [f"semantic error {e.code}: {e.message}" for e in result.errors]
    problems.extend(_CHECKERS[lesson.number - 1](program))

    if problems:
        typer.echo(
            f"[FAIL] Lesson {number} ({lesson.title}): {path} does not "
            "solve the task yet:"
        )
        for problem in problems:
            typer.echo(f"  - {problem}")
        return False
    typer.echo(
        f"[OK] Lesson {number} ({lesson.title}): {path} solves the task. "
        "Well done!"
    )
    return True


# --------------------------------------------------------------------- #
# Interactive walk-through (default, no flags)
# --------------------------------------------------------------------- #


def _interactive() -> None:
    """Walk through all five lessons with a prompt between them."""
    typer.echo("infra learn - interactive .infra tutorial (5 lessons)")
    typer.echo("Press [Enter] to advance, type 'q' to quit.\n")
    for lesson in LESSONS:
        typer.echo(render_lesson(lesson))
        typer.echo()
        try:
            answer = input(">>> [Enter] next lesson, 'q' to quit: ")
        except (EOFError, KeyboardInterrupt):
            typer.echo("\n[OK] See you next time.")
            raise typer.Exit() from None
        if answer.strip().lower() == "q":
            typer.echo("\n[OK] See you next time.")
            raise typer.Exit() from None
        typer.echo()
    typer.echo("[OK] All 5 lessons shown. Keep experimenting: infra learn --list")


# --------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------- #


def learn(
    path: Optional[Path] = typer.Argument(
        None, help="Your solution .infra file (used with --verify)."
    ),
    list_lessons: bool = typer.Option(
        False, "--list", help="List the available lessons and exit."
    ),
    lesson: Optional[int] = typer.Option(
        None, "--lesson", help="Print lesson N (1-5) and exit."
    ),
    verify: Optional[int] = typer.Option(
        None, "--verify", help="Verify a solution file for lesson N."
    ),
) -> None:
    """Learn the .infra language interactively, right in your terminal.

    Five bite-sized lessons take you from a single service to advanced
    network policies: theory, runnable examples and a task checked with
    the real parser and validator.
    """
    if verify is not None:
        if path is None:
            typer.echo(
                "[FAIL] --verify needs a solution file: "
                "infra learn <file.infra> --verify <N>",
                err=True,
            )
            raise typer.Exit(code=1)
        if not verify_solution(verify, path):
            raise typer.Exit(code=1)
        return
    if list_lessons:
        typer.echo(render_lesson_list())
        return
    if lesson is not None:
        found = _lesson_by_number(lesson)
        if found is None:
            typer.echo(
                f"[FAIL] Unknown lesson {lesson} (valid: 1-{len(LESSONS)}).",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(render_lesson(found))
        return
    _interactive()
