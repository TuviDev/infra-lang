"""Symbol table and scopes for semantic analysis."""
# ruff: noqa: N802, N812

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from infra.analyzer import types as T
from infra.parser import ast_nodes as n


class SymbolKind(str, Enum):
    VARIABLE = "variable"
    CONST = "const"
    SERVICE = "service"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"
    STORAGE = "storage"
    NETWORK = "network"
    SECRET = "secret"
    CONFIG = "config"
    PIPELINE = "pipeline"
    ENVIRONMENT = "environment"
    CLUSTER = "cluster"
    BUILTIN = "builtin"


class ScopeKind(str, Enum):
    GLOBAL = "global"
    BLOCK = "block"
    SERVICE = "service"
    PIPELINE = "pipeline"
    ENVIRONMENT = "environment"


class SymbolAlreadyDefinedError(Exception):
    """Raised when a symbol is defined twice in the same scope."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Symbol '{name}' is already defined in this scope")
        self.name = name


class Symbol:
    """A single named symbol."""

    def __init__(
        self,
        name: str,
        type: T.InfraType,
        definition: Optional[n.ASTNode] = None,
        kind: SymbolKind = SymbolKind.VARIABLE,
        mutable: bool = True,
    ) -> None:
        self.name = name
        self.type = type
        self.definition = definition
        self.kind = kind
        self.mutable = mutable
        self.used = False

    def __repr__(self) -> str:  # pragma: no cover - debugging
        return f"Symbol({self.name!r}, {self.kind.value})"


class Scope:
    """A lexical scope holding symbols."""

    def __init__(
        self, kind: ScopeKind = ScopeKind.BLOCK, parent: Optional["Scope"] = None
    ) -> None:
        self.parent = parent
        self.symbols: Dict[str, Symbol] = {}
        self.scope_kind = kind

    def define(self, name: str, symbol: Symbol) -> None:
        if name in self.symbols:
            raise SymbolAlreadyDefinedError(name)
        self.symbols[name] = symbol

    def lookup(self, name: str) -> Optional[Symbol]:
        scope: Optional[Scope] = self
        while scope is not None:
            if name in scope.symbols:
                return scope.symbols[name]
            scope = scope.parent
        return None

    def lookup_local(self, name: str) -> Optional[Symbol]:
        return self.symbols.get(name)

    def child(self, kind: ScopeKind = ScopeKind.BLOCK) -> "Scope":
        return Scope(kind=kind, parent=self)


class SymbolTable:
    """Manages scopes and symbol definitions during a validation pass."""

    def __init__(self) -> None:
        self.global_scope = Scope(kind=ScopeKind.GLOBAL)
        self.current_scope = self.global_scope
        self._scope_stack: list[Scope] = [self.global_scope]
        self.register_builtins()

    # -- context manager --------------------------------------------------- #
    def enter_scope(self, kind: ScopeKind = ScopeKind.BLOCK) -> "Scope":
        scope = self.current_scope.child(kind)
        self.current_scope = scope
        self._scope_stack.append(scope)
        return scope

    def exit_scope(self) -> None:
        if len(self._scope_stack) > 1:
            self._scope_stack.pop()
            self.current_scope = self._scope_stack[-1]

    def __enter__(self) -> "Scope":
        return self.enter_scope()

    def __exit__(self, *exc: Any) -> None:
        self.exit_scope()

    # -- define / lookup --------------------------------------------------- #
    def define(self, symbol: Symbol) -> None:
        self.current_scope.define(symbol.name, symbol)

    def lookup(self, name: str) -> Optional[Symbol]:
        return self.current_scope.lookup(name)

    def lookup_local(self, name: str) -> Optional[Symbol]:
        return self.current_scope.lookup_local(name)

    def get_all_definitions(self) -> Dict[str, Symbol]:
        return dict(self.global_scope.symbols)

    def register_builtins(self) -> None:
        """Register built-in functions and constants in the global scope."""
        from infra.stdlib.functions import STDLIB

        for fn in STDLIB.all_names():
            self.global_scope.symbols[fn] = Symbol(
                fn, STDLIB.get(fn).return_type, kind=SymbolKind.BUILTIN, mutable=False
            )
        for extra in ("secret", "config", "version"):
            self.global_scope.symbols[extra] = Symbol(
                extra, T.ANY, kind=SymbolKind.BUILTIN, mutable=False
            )
        for const in ("ENV", "NAMESPACE", "CLUSTER_NAME"):
            self.global_scope.symbols[const] = Symbol(
                const, T.STRING, kind=SymbolKind.BUILTIN, mutable=False
            )
