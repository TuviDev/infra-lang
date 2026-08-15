"""Import resolver — loads imported ``.infra`` files and merges them.

Handles ``import "./x.infra"``, ``import ... as alias`` and
``from "./x.infra" import A, B``. Symbols from imported files become available
to the rest of the program.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lark import Lark
from lark.exceptions import UnexpectedInput

from infra.errors.exceptions import InfraError
from infra.parser import ast_nodes as n
from infra.parser.transformer import InfraTransformer


class ImportCycleError(InfraError):  # type: ignore[misc]
    """Raised when imports form a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Circular import detected: {' -> '.join(cycle)}")


class ImportResolver:
    def __init__(self, base_path: Optional[Path] = None, max_depth: int = 10) -> None:
        self.base_path = base_path or Path.cwd()
        self.max_depth = max_depth
        self._loading: list[Path] = []
        self._cache: dict[Path, n.Program] = {}

    def resolve(self, program: n.Program, current_file: Path) -> n.Program:
        all_stmts = []
        for imp in program.imports:
            resolved = self._resolve_import(imp, current_file, depth=0)
            if resolved:
                all_stmts.extend(resolved.statements)
        merged_stmts = tuple(all_stmts) + program.statements
        return n.Program(statements=merged_stmts, imports=program.imports)

    def _resolve_import(
        self, imp: n.Import, current_file: Path, depth: int
    ) -> Optional[n.Program]:
        if depth > self.max_depth:
            return None
        file_path = self._find_file(imp.path, current_file)
        if file_path is None:
            raise FileNotFoundError(
                f"Import not found: {imp.path} (from {current_file})"
            )
        abs_path = file_path.resolve()
        if abs_path in self._loading:
            cycle = [str(p) for p in self._loading] + [str(abs_path)]
            raise ImportCycleError(cycle)
        if abs_path in self._cache:
            return self._cache[abs_path]

        self._loading.append(abs_path)
        try:
            source = abs_path.read_text(encoding="utf-8")
            program = self._parse_raw(source, abs_path.name)
            resolved = self.resolve(program, abs_path)
            # apply selective `from ... import` filtering
            resolved = self._filter_from_import(resolved, imp)
            self._cache[abs_path] = resolved
            return resolved
        finally:
            self._loading.remove(abs_path)

    def _filter_from_import(self, program: n.Program, imp: n.Import) -> n.Program:
        """For ``from ... import A, B``, keep only the requested declarations."""
        if not imp.names:
            return program
        wanted = set(imp.names)
        filtered = [
            s
            for s in program.statements
            if isinstance(s, n.VariableDecl) and s.name in wanted
        ]
        return n.Program(statements=tuple(filtered), imports=program.imports)

    def _find_file(self, path_str: str, current_file: Path) -> Optional[Path]:
        candidates = [
            current_file.parent / path_str,
            self.base_path / path_str,
            Path(path_str),
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    def _parse_raw(self, source: str, filename: str) -> n.Program:
        from infra.parser import DEFAULT_GRAMMAR

        try:
            parser = Lark(
                DEFAULT_GRAMMAR.read_text(),
                parser="lalr",
                propagate_positions=True,
            )
            tree = parser.parse(source)
            transformer = InfraTransformer()
            program = transformer.transform(tree)
            if not isinstance(program, n.Program):
                program = n.Program(
                    statements=(program,) if program is not None else ()
                )
            return program
        except UnexpectedInput as e:
            from infra.errors.exceptions import InfraParseError

            raise InfraParseError(str(e), source=source) from e
