"""Built-in functions available in every .infra file.

Registered as BUILTIN symbols and dispatched from ``evaluate_expression``.
Each function has a name, description, params, return type, a compile handler
(what it emits into backend output) and an evaluate handler (REPL/literal use).
"""
# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,type-arg"
# ruff: noqa: N802, N812

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from infra.analyzer import types as T
from infra.errors.exceptions import InfraRuntimeError


@dataclass(frozen=True)
class ParamDef:
    name: str
    type: T.InfraType = T.ANY
    optional: bool = False
    default: Any = None


@dataclass
class BuiltinFunction:
    name: str
    description: str
    params: List[ParamDef]
    return_type: T.InfraType = T.ANY
    compile_handler: Callable[..., Any] = field(default=lambda *a, **k: None)
    evaluate_handler: Callable[..., Any] = field(default=lambda *a, **k: None)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


def _env(name, default=None):
    if default is not None:
        return os.environ.get(name, default)
    return os.environ.get(name, f"${{{name}}}")


def _env_compile(name, default=None):
    if default is not None:
        return f"${{{name}:-{default}}}"
    return f"${{{name}}}"


def _upper(s):
    return str(s).upper()


def _lower(s):
    return str(s).lower()


def _trim(s):
    return str(s).strip()


def _replace(s, old, new):
    return str(s).replace(str(old), str(new))


def _contains(s, sub):
    return sub in s


def _starts_with(s, prefix):
    return str(s).startswith(str(prefix))


def _ends_with(s, suffix):
    return str(s).endswith(str(suffix))


def _split(s, sep):
    return str(s).split(str(sep))


def _join(items, sep):
    return str(sep).join(str(i) for i in items)


def _len(v):
    return len(v)


def _min(a, b):
    return min(a, b)


def _max(a, b):
    return max(a, b)


def _abs(a):
    return abs(a)


def _clamp(v, lo, hi):
    return max(lo, min(v, hi))


def _length(lst):
    return len(lst)


def _concat(a, b):
    return list(a) + list(b)


def _first(lst):
    return lst[0] if lst else None


def _last(lst):
    return lst[-1] if lst else None


def _list_contains(lst, item):
    return item in lst


def _range(n):
    return list(range(int(n)))


def _coalesce(a, b):
    return a if a is not None else b


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class StdlibRegistry:
    def __init__(self) -> None:
        self._functions: dict[str, BuiltinFunction] = {}

    def register(self, fn: BuiltinFunction) -> None:
        self._functions[fn.name] = fn

    def get(self, name: str) -> Optional[BuiltinFunction]:
        return self._functions.get(name)

    def all_names(self) -> List[str]:
        return list(self._functions.keys())

    def is_builtin(self, name: str) -> bool:
        return name in self._functions


STDLIB = StdlibRegistry()

_str_params = [ParamDef("s", T.STRING)]


def _reg(name, desc, params, handler, ret=T.ANY):
    STDLIB.register(
        BuiltinFunction(
            name=name,
            description=desc,
            params=params,
            return_type=ret,
            evaluate_handler=handler,
        )
    )


_reg(
    "env",
    "Get an environment variable",
    [ParamDef("name", T.STRING), ParamDef("default", T.STRING, True)],
    _env,
)
_reg("upper", "Uppercase a string", _str_params, _upper)
_reg("lower", "Lowercase a string", _str_params, _lower)
_reg("trim", "Trim whitespace", _str_params, _trim)
_reg(
    "replace",
    "Replace substring",
    [ParamDef("s", T.STRING), ParamDef("from", T.STRING), ParamDef("to", T.STRING)],
    _replace,
)
_reg(
    "contains",
    "Check substring",
    [ParamDef("s", T.STRING), ParamDef("sub", T.STRING)],
    _contains,
    T.BOOL,
)
_reg(
    "starts_with",
    "Prefix check",
    [ParamDef("s", T.STRING), ParamDef("prefix", T.STRING)],
    _starts_with,
    T.BOOL,
)
_reg(
    "ends_with",
    "Suffix check",
    [ParamDef("s", T.STRING), ParamDef("suffix", T.STRING)],
    _ends_with,
    T.BOOL,
)
_reg(
    "split",
    "Split on separator",
    [ParamDef("s", T.STRING), ParamDef("sep", T.STRING)],
    _split,
    T.ListType(T.STRING),
)
_reg(
    "join",
    "Join with separator",
    [ParamDef("items", T.ListType(T.ANY)), ParamDef("sep", T.STRING)],
    _join,
    T.STRING,
)
_reg("len", "Length of string/list", [ParamDef("v", T.ANY)], _len, T.INT)
_reg("min", "Minimum", [ParamDef("a", T.INT), ParamDef("b", T.INT)], _min, T.INT)
_reg("max", "Maximum", [ParamDef("a", T.INT), ParamDef("b", T.INT)], _max, T.INT)
_reg("abs", "Absolute value", [ParamDef("a", T.INT)], _abs, T.INT)
_reg(
    "clamp",
    "Clamp value",
    [ParamDef("val", T.INT), ParamDef("min", T.INT), ParamDef("max", T.INT)],
    _clamp,
    T.INT,
)
_reg("length", "List length", [ParamDef("l", T.ListType(T.ANY))], _length, T.INT)
_reg(
    "concat",
    "Concatenate lists",
    [ParamDef("a", T.ListType(T.ANY)), ParamDef("b", T.ListType(T.ANY))],
    _concat,
    T.ListType(T.ANY),
)
_reg("first", "First element", [ParamDef("l", T.ListType(T.ANY))], _first)
_reg("last", "Last element", [ParamDef("l", T.ListType(T.ANY))], _last)
_reg("range", "Range 0..n-1", [ParamDef("n", T.INT)], _range, T.ListType(T.INT))
_reg(
    "coalesce",
    "First non-null",
    [ParamDef("a", T.ANY), ParamDef("b", T.ANY)],
    _coalesce,
)
_reg(
    "if_env",
    "Conditional on env",
    [ParamDef("name", T.STRING), ParamDef("then", T.ANY), ParamDef("else", T.ANY)],
    lambda name, t, e: (t if os.environ.get(name) else e),
)


def call_builtin(name: str, args: List[Any]) -> Any:
    """Call a registered builtin by name with evaluated args."""
    fn = STDLIB.get(name)
    if fn is None:
        raise InfraRuntimeError(f"Unknown builtin function '{name}'", expression=name)
    try:
        return fn.evaluate_handler(*args)
    except TypeError as exc:
        raise InfraRuntimeError(f"Bad arguments to '{name}': {exc}", expression=name)
