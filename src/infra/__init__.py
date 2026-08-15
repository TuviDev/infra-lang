"""Infra — a DSL for defining cloud infrastructure.

Public entry points:
    parse / parse_file      -> compile source to an AST
    validate                -> run semantic checks
    compile                 -> emit YAML/HCL for a chosen backend
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from infra.version import VERSION_INFO, __version__

if TYPE_CHECKING:  # pragma: no cover
    from infra.analyzer.validator import ValidationResult
    from infra.backends.base import CompileResult
    from infra.parser import ast_nodes as n


def parse(source: str, filename: str = "<string>") -> "n.Program":
    """Parse Infra source text into a Program AST."""
    from infra.parser import parse as _parse

    return _parse(source, filename)


def parse_file(path: Union[str, Path]) -> "n.Program":
    """Parse an Infra source file into a Program AST."""
    from infra.parser import parse_file as _parse_file

    return _parse_file(Path(path))


def validate(source_or_program: Any, filename: str = "<string>") -> "ValidationResult":
    """Run semantic validation, returning a ValidationResult."""
    from infra.analyzer.validator import SemanticValidator
    from infra.parser import ast_nodes as n

    if isinstance(source_or_program, n.Program):
        program = source_or_program
    else:
        program = parse(source_or_program, filename)
    from infra.resolver.extends import ExtendsResolver

    program = ExtendsResolver().resolve(program)
    return SemanticValidator().validate(program)


def compile(
    source_or_program: Any,
    target: str = "kubernetes",
    filename: str = "<string>",
    **backend_opts: Any,
) -> "CompileResult":
    """Compile a Program (or source text) with the chosen backend.

    ``target`` is one of: kubernetes, compose, terraform, github.
    """
    from infra.backends import get_backend
    from infra.parser import ast_nodes as n

    if isinstance(source_or_program, n.Program):
        program = source_or_program
    else:
        program = parse(source_or_program, filename)
    backend = get_backend(target, **backend_opts)
    return backend.compile(program)


__all__ = [
    "parse",
    "parse_file",
    "validate",
    "compile",
    "__version__",
    "VERSION_INFO",
]
