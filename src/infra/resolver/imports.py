"""Import resolver — loads imported ``.infra`` files and merges them.

Handles ``import "./x.infra"``, ``import ... as alias`` and
``from "./x.infra" import A, B``. Symbols from imported files become available
to the rest of the program.

Resolution invariants:

* **Cycles** are rejected via the in-progress import stack (:attr:`_loading`),
  raising :class:`ImportCycleError` with the full cycle path.
* **Diamonds** (two importers pulling the same file) are deduplicated via
  :attr:`_visited_files`: a file already merged into the root AST — with the
  same selection — is skipped silently, so a shared ``const`` trips no false
  ``E001`` duplicate-definition error. The per-``resolve`` merge dedup is the
  statement-identity safety net that keeps selective ``from ... import``
  unions correct: when two imports request overlapping name sets of the same
  file, each shared statement is still merged exactly once (the guard is
  local to each merge level, because nested levels legitimately propagate
  the same node objects upward).
* **Depth** is bounded by :attr:`max_depth` (default
  :data:`DEFAULT_MAX_DEPTH` = 20 nested import hops); exceeding it raises
  :class:`ImportDepthError` — a domain error — never Python's opaque
  ``RecursionError``.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set, Tuple, Union

from lark.exceptions import UnexpectedInput

from infra.errors.exceptions import InfraError
from infra.parser import ast_nodes as n
from infra.parser.transformer import InfraTransformer


class ImportCycleError(InfraError):
    """Raised when imports form a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Circular import detected: {' -> '.join(cycle)}")


#: Hard limit of nested import hops (the root file is not counted).
DEFAULT_MAX_DEPTH = 20


class ImportDepthError(InfraError):
    """Raised when an import chain exceeds the depth limit.

    Domain error raised instead of Python's opaque ``RecursionError`` when
    a chain of ``import`` statements nests deeper than
    :data:`DEFAULT_MAX_DEPTH`.
    """


class ImportResolver:
    def __init__(
        self,
        base_path: Optional[Path] = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self.base_path = base_path or Path.cwd()
        self.max_depth = max_depth
        #: in-progress import stack — cycle detection (`visited_chain`).
        self._loading: list[Path] = []
        #: path -> resolved (unfiltered) program; also anchors the node
        #: objects, so id()-based statement identities stay stable.
        self._cache: dict[Path, n.Program] = {}
        #: (file, requested names or None for a full import) already merged
        #: into the root AST — the diamond dedup guard.
        self._visited_files: Set[Tuple[Path, Optional[Tuple[str, ...]]]] = set()

    def resolve(
        self,
        program: n.Program,
        current_file: Path,
        *,
        depth: int = 0,
    ) -> n.Program:
        """Merge the imports of *program* (the program of *current_file*).

        *depth* is the nesting level of *current_file* (0 for the root
        file); each imported file lives one level deeper.
        """
        all_stmts: List[Union[n.Statement, n.Definition]] = []
        #: id() of statements already merged at THIS level — the safety net
        #: for overlapping ``from ... import`` selections of the same file.
        #: Local to each merge: nested levels legitimately propagate the same
        #: node objects upward, so a shared instance set would wrongly drop
        #: them (identical *copies* from different files are a genuine
        #: duplicate definition and must still error).
        merged_stmt_ids: Set[int] = set()
        for imp in program.imports:
            resolved = self._resolve_import(imp, current_file, depth)
            if resolved is None:
                continue
            for stmt in resolved.statements:
                if id(stmt) in merged_stmt_ids:
                    continue
                merged_stmt_ids.add(id(stmt))
                all_stmts.append(stmt)
        merged_stmts = tuple(all_stmts) + program.statements
        return n.Program(
            statements=merged_stmts,
            imports=program.imports,
            environments=program.environments,
        )

    def _resolve_import(
        self, imp: n.Import, current_file: Path, depth: int
    ) -> Optional[n.Program]:
        # The imported file lives one nesting level deeper than current_file;
        # fail with a domain error instead of Python's RecursionError.
        if depth + 1 > self.max_depth:
            raise ImportDepthError(
                f"Import depth exceeded limit of {self.max_depth}"
            )
        file_path = self._find_file(imp.path, current_file)
        if file_path is None:
            raise FileNotFoundError(
                f"Import not found: {imp.path} (from {current_file})"
            )
        abs_path = file_path.resolve()
        if abs_path in self._loading:
            cycle = [str(p) for p in self._loading] + [str(abs_path)]
            raise ImportCycleError(cycle)

        # Diamond dedup key: the file plus the requested selection
        # (None = full import). Two imports with the *same* selection skip;
        # differing selections still merge — `_merged_stmt_ids` then keeps
        # each shared statement from being duplicated.
        select: Optional[Tuple[str, ...]] = (
            tuple(sorted(imp.names)) if imp.names else None
        )
        if (abs_path, select) in self._visited_files:
            return None

        if abs_path in self._cache:
            resolved_raw = self._cache[abs_path]
        else:
            self._loading.append(abs_path)
            try:
                source = abs_path.read_text(encoding="utf-8")
                program = self._parse_raw(source, abs_path.name)
                resolved_raw = self.resolve(program, abs_path, depth=depth + 1)
                # Cache the RAW (unfiltered) program: a later import with a
                # different `from ... import` selection must still see every
                # declaration of the file.
                self._cache[abs_path] = resolved_raw
            finally:
                self._loading.remove(abs_path)

        self._visited_files.add((abs_path, select))
        # apply selective `from ... import` filtering
        return self._filter_from_import(resolved_raw, imp)

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
        from infra.parser import _raw_lark

        try:
            # Shared process-wide Lark instance: the grammar is compiled
            # once instead of once per imported file.
            tree = _raw_lark().parse(source)
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
