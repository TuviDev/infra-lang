"""The Infra type system.

Primitive, composite and special types, plus compatibility and unification
helpers used by the semantic validator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple


class InfraType:
    """Base class for all types."""

    def is_compatible(self, other: "InfraType") -> bool:
        """True if a value of *other* can be used where this type is expected."""
        return self.is_assignable_from(other) or other.is_assignable_from(self)

    def is_assignable_from(self, other: "InfraType") -> bool:
        """True if *other* can be assigned to a slot of this type."""
        return isinstance(other, type(self))

    def __str__(self) -> str:
        return self.__class__.__name__.replace("Type", "").lower()


# --------------------------------------------------------------------------- #
# Primitive types
# --------------------------------------------------------------------------- #


class StringType(InfraType):
    def __str__(self) -> str:
        return "string"


class IntType(InfraType):
    def __str__(self) -> str:
        return "int"

    def is_assignable_from(self, other: InfraType) -> bool:
        # ints can be used as floats
        return super().is_assignable_from(other) or isinstance(other, FloatType)


class FloatType(InfraType):
    def __str__(self) -> str:
        return "float"


class BoolType(InfraType):
    def __str__(self) -> str:
        return "bool"


class NullType(InfraType):
    def __str__(self) -> str:
        return "null"

    def is_assignable_from(self, other: InfraType) -> bool:
        return isinstance(other, NullType)


class DurationType(InfraType):
    def __str__(self) -> str:
        return "duration"


class ResourceValueType(InfraType):
    def __str__(self) -> str:
        return "resource"


class PercentageType(InfraType):
    def __str__(self) -> str:
        return "percentage"


# --------------------------------------------------------------------------- #
# Composite types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ListType(InfraType):
    element_type: InfraType

    def is_assignable_from(self, other: InfraType) -> bool:
        return isinstance(other, ListType) and (
            self.element_type.is_assignable_from(other.element_type)
        )

    def __str__(self) -> str:
        return f"list[{self.element_type}]"


@dataclass(frozen=True)
class MapType(InfraType):
    key_type: InfraType
    value_type: InfraType

    def is_assignable_from(self, other: InfraType) -> bool:
        return isinstance(other, MapType)

    def __str__(self) -> str:
        return f"map[{self.key_type}, {self.value_type}]"


@dataclass(frozen=True)
class UnionType(InfraType):
    types: Tuple[InfraType, ...]

    def is_assignable_from(self, other: InfraType) -> bool:
        return any(t.is_assignable_from(other) for t in self.types)

    def __str__(self) -> str:
        return " | ".join(str(t) for t in self.types)


@dataclass(frozen=True)
class OptionalType(InfraType):
    inner: InfraType

    def is_assignable_from(self, other: InfraType) -> bool:
        return self.inner.is_assignable_from(other) or isinstance(other, NullType)

    def __str__(self) -> str:
        return f"{self.inner}?"


# --------------------------------------------------------------------------- #
# Special types
# --------------------------------------------------------------------------- #


class AnyType(InfraType):
    def is_assignable_from(self, other: InfraType) -> bool:
        return True

    def is_compatible(self, other: InfraType) -> bool:
        return True

    def __str__(self) -> str:
        return "any"


class UnknownType(InfraType):
    """A type not yet inferred."""

    def is_assignable_from(self, other: InfraType) -> bool:
        return True

    def __str__(self) -> str:
        return "unknown"


class ErrorType(InfraType):
    """Used for error recovery; compatible with everything."""

    def is_assignable_from(self, other: InfraType) -> bool:
        return True

    def is_compatible(self, other: InfraType) -> bool:
        return True

    def __str__(self) -> str:
        return "error"


# --------------------------------------------------------------------------- #
# Singleton constants
# --------------------------------------------------------------------------- #

STRING = StringType()
INT = IntType()
FLOAT = FloatType()
BOOL = BoolType()
NULL = NullType()
DURATION = DurationType()
RESOURCE = ResourceValueType()
PERCENTAGE = PercentageType()
ANY = AnyType()
UNKNOWN = UnknownType()
ERROR = ErrorType()


def infer_literal_type(value: object) -> InfraType:
    """Infer the type of a Python literal value."""
    if value is None:
        return NULL
    if isinstance(value, bool):
        return BOOL
    if isinstance(value, int):
        return INT
    if isinstance(value, float):
        return FLOAT
    if isinstance(value, str):
        return STRING
    return ANY


def are_types_compatible(a: InfraType, b: InfraType) -> bool:
    """True if *a* and *b* are mutually compatible."""
    return a.is_compatible(b)


def unify_types(types: Sequence[InfraType]) -> InfraType:
    """Find a common supertype for a sequence of types."""
    if not types:
        return ANY
    if len(types) == 1:
        return types[0]
    # If all are the same primitive, return it.
    first = types[0]
    if all(type(t) is type(first) for t in types):
        return first
    # If any is Any/Unknown, collapse to that.
    for t in types:
        if isinstance(t, (AnyType, UnknownType, ErrorType)):
            return t
    # Numeric family
    if all(isinstance(t, (IntType, FloatType)) for t in types):
        if any(isinstance(t, FloatType) for t in types):
            return FLOAT
        return INT
    # Otherwise a union.
    return UnionType(tuple(types))
