"""Vulture whitelist for Typer CLI option variables.

Typer registers a CLI flag for every function parameter it can read from the
function signature (option name defaults to the parameter name). The variables
below are declared as `typer.Option(...)` parameters, so they ARE part of the
public `infra` CLI surface — Vulture flags them as "unused" because it does not
understand Typer's introspection. Removing them would break the CLI flags.

These assignments tell Vulture the names are intentional (they mirror the
parameter names that Typer binds), so `vulture src/infra --whitelist
.vulture_whitelist.py` reports no findings for them.
"""

# `infra compile --environment`
environment = None  # noqa: F841
# `infra feedback --project`
project = None  # noqa: F841
# `infra --no-color`
no_color = None  # noqa: F841
