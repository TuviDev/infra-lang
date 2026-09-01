"""Infra CLI application."""

from __future__ import annotations

import typer

from infra.version import __version__

app = typer.Typer(
    name="infra",
    help="Infra Language — define cloud infrastructure and compile it to "
    "Kubernetes YAML, Docker Compose, Terraform HCL and GitHub Actions.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"infra {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
    quiet: bool = typer.Option(False, "--quiet", help="Only print errors."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
) -> None:
    """Global options for the infra CLI."""
    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)
    if quiet:
        import logging

        logging.getLogger().setLevel(logging.ERROR)


def register_commands() -> None:
    from infra.cli import check, diff, docs, fmt, graph, init, repl, validate
    from infra.cli import compile as compile_cmd
    from infra.cli.alert_cmd import alert_cmd
    from infra.cli.ci_comment import ci_comment_cmd
    from infra.cli.cost_cmd import cost_cmd
    from infra.cli.doctor import doctor
    from infra.cli.feedback_cmd import feedback_cmd
    from infra.cli.import_cmd import import_cmd
    from infra.cli.learn_cmd import learn
    from infra.cli.lsp_cmd import lsp_cmd
    from infra.cli.policy_cmd import policy_check_cmd
    from infra.cli.schema_cmd import schema
    from infra.cli.serve_cmd import serve_cmd
    from infra.cli.up_cmd import down, up

    app.command(name="up", help="Compile and deploy a .infra file.")(up)
    app.command(name="down", help="Remove resources applied from a .infra file.")(down)
    app.command(name="cost", help="Estimate monthly cloud cost of a .infra file.")(
        cost_cmd
    )

    app.command(name="compile", help="Compile .infra files.")(compile_cmd.compile)
    app.command(name="validate", help="Validate .infra files without compiling.")(
        validate.validate
    )
    app.command(name="fmt", help="Format .infra files.")(fmt.fmt)
    app.command(name="repl", help="Start an interactive REPL.")(repl.repl)
    app.command(name="init", help="Create a new Infra project.")(init.init)
    app.command(name="lsp", help="Start the language server.")(lsp_cmd)
    app.command(
        name="feedback", help="Manage opt-in anonymous error reporting."
    )(feedback_cmd)
    app.command(
        name="import",
        help="Import existing Kubernetes YAML and generate .infra source.",
    )(import_cmd)
    app.command(name="check", help="Quick syntax check (no semantics).")(check.check)
    app.command(
        name="alert",
        help="Send Slack/Teams/Discord alerts (drift, cost, security).",
    )(alert_cmd)
    app.command(
        name="policy-check",
        help="Check a .infra file against declarative YAML policy rules.",
    )(policy_check_cmd)
    app.command(
        name="ci-comment",
        help="Generate a PR-ready CI comment (changes, cost delta, SEC*/REL*).",
    )(ci_comment_cmd)
    app.command(name="graph", help="Print the dependency graph.")(graph.graph)
    app.command(
        name="schema", help="Export the JSON Schema of the .infra DSL."
    )(schema)
    app.command(
        name="learn", help="Interactive .infra DSL tutorial (5 lessons)."
    )(learn)
    app.command(name="docs", help="Generate documentation from .infra files.")(
        docs.docs
    )
    app.command(
        name="diff",
        help="Compare two .infra files, or plan against live infra (--live).",
    )(diff.diff_cmd)
    app.command(name="doctor", help="Check the local environment for needed tools.")(
        doctor
    )
    app.command(
        name="serve",
        help="Open the interactive local dashboard (architecture DAG, FinOps, drift).",
    )(serve_cmd)
    app.command(
        name="ui", help="Alias for 'infra serve' (visual dashboard)."
    )(serve_cmd)


register_commands()


if __name__ == "__main__":
    app()
