# ruff: noqa: E501
"""AST pretty-printer: regenerates Infra source from a Program AST.

Used by the ``infra fmt`` command. Whitespace inside string/template
literals is preserved; structural formatting is normalized.
"""
# mypy: disable-error-code="no-untyped-def,no-untyped-call,no-any-return,type-arg,misc,union-attr"

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from infra.parser import _parser
from infra.parser import ast_nodes as n

_Q = '""'


def _safe_bare(value: str) -> bool:
    """True if *value* can be emitted as a bare (unquoted) Infra token."""
    import re

    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*|\d+(\.\d+)?", value) is not None


def _qstr(value: Optional[str]) -> str:
    """Emit a string, quoting it only when it cannot be a bare token."""
    if value is None:
        return _Q
    return value if _safe_bare(value) else f'"{value}"'


class InfraPrinter:
    def __init__(self, indent: int = 4) -> None:
        self.indent = indent
        self.out: list[str] = []
        self.depth = 0

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _w(self, text: str = "") -> None:
        self.out.append(" " * (self.depth * self.indent) + text)

    def _block(self, head: str, body: list[str]) -> None:
        self._w(head + " {")
        self.depth += 1
        for line in body:
            self._w(line)
        self.depth -= 1
        self._w("}")

    def _join(self, items: Sequence[str]) -> str:
        return ", ".join(items)

    # ------------------------------------------------------------------ #
    # Expressions
    # ------------------------------------------------------------------ #

    def _expr(self, e: Optional[n.Expression]) -> str:
        if e is None:
            return ""
        if isinstance(e, n.Literal):
            v = e.value
            if isinstance(v, str):
                return f'"{v}"'
            if v is None:
                return "null"
            if isinstance(v, bool):
                return "true" if v else "false"
            return str(v)
        if isinstance(e, n.Identifier):
            return e.name
        if isinstance(e, n.Duration):
            return self._num(e.value) + e.unit
        if isinstance(e, n.ResourceValue):
            return self._num(e.value) + e.unit
        if isinstance(e, n.Percentage):
            return self._num(e.value) + "%"
        if isinstance(e, n.BinaryOp):
            return f"{self._expr(e.left)} {e.operator} {self._expr(e.right)}"
        if isinstance(e, n.UnaryOp):
            return f"{e.operator}{self._expr(e.operand)}"
        if isinstance(e, n.Call):
            args = [self._expr(a) for a in e.args]
            args += [f"{k} = {self._expr(v)}" for k, v in e.kwargs]
            return f"{self._expr(e.callee)}({self._join(args)})"
        if isinstance(e, n.Attribute):
            return f"{self._expr(e.obj)}.{e.attr}"
        if isinstance(e, n.Index):
            return f"{self._expr(e.obj)}[{self._expr(e.index)}]"
        if isinstance(e, n.List):
            items = [self._expr(i) for i in e.items]
            if len(items) <= 3 and len(", ".join(items)) <= 60:
                return f"[{self._join(items)}]"
            inner = "\n".join(" " * (self.indent) + x for x in items)
            return f"[\n{inner}\n]"
        if isinstance(e, n.Map):
            parts = [
                f"{self._expr(en.key)}: {self._expr(en.value)}" for en in e.entries
            ]
            if len(parts) <= 3 and len(", ".join(parts)) <= 60:
                return f"{{ {self._join(parts)} }}"
            inner = "\n".join(" " * self.indent + x for x in parts)
            return f"{{\n{inner}\n}}"
        if isinstance(e, n.TemplateString):
            parts = []
            for p in e.parts:
                if isinstance(p, str):
                    parts.append(p)
                elif isinstance(p, tuple) and p and p[0] == "expr":
                    parts.append(f"{{{p[1]}}}")
                else:
                    parts.append(str(p))
            return "`" + "".join(parts) + "`"
        if isinstance(e, n.IfExpr):
            return f"if {self._expr(e.condition)} then {self._expr(e.then_branch)} else {self._expr(e.else_branch)}"  # noqa: E501
        if isinstance(e, n.MatchExpr):
            arms = [
                f"  {self._pattern(a.pattern)} -> {self._expr(a.body)}" for a in e.arms
            ]
            return "match " + self._expr(e.subject) + " {\n" + "\n".join(arms) + "\n}"
        return str(e)

    @staticmethod
    def _pattern(p) -> str:
        if p is None:
            return "_"
        if isinstance(p, n.Literal):
            return str(p.value)
        if isinstance(p, n.Identifier):
            return p.name
        return str(p)

    @staticmethod
    def _num(v: float) -> str:
        if v == int(v):
            return str(int(v))
        return str(v)

    def _str_list(self, items) -> list[str]:
        if isinstance(items, n.List):
            return [self._expr(i) for i in items.items]
        return [str(i) for i in items]

    # ------------------------------------------------------------------ #
    # Definitions
    # ------------------------------------------------------------------ #

    def print(self, program: n.Program) -> str:
        self.out = []
        blocks: list[str] = []
        for imp in program.imports:
            blocks.append(self._import(imp))
        for stmt in program.statements:
            # skip prelude constants (auto-loaded, not part of the user file)
            if getattr(getattr(stmt, "location", None), "file", "") == "<prelude>":
                continue
            blocks.append(self._stmt(stmt))
        # separate top-level blocks by 2 blank lines
        text = "\n\n".join(b for b in blocks if b)
        return text.rstrip() + "\n"

    def _import(self, imp: n.Import) -> str:
        if imp.names:
            return f'from "{imp.path}" import {", ".join(imp.names)}'
        if imp.alias:
            return f'import "{imp.path}" as {imp.alias}'
        return f'import "{imp.path}"'

    def _stmt(self, stmt: n.ASTNode) -> str:
        if isinstance(stmt, n.Import):
            return self._import(stmt)
        if isinstance(stmt, n.VariableDecl):
            kw = "const" if stmt.const else "let"
            return f"{kw} {stmt.name} = {self._expr(stmt.value)}"
        if isinstance(stmt, n.ServiceDef):
            return self._service(stmt)
        if isinstance(stmt, n.DatabaseDef):
            return self._database(stmt)
        if isinstance(stmt, n.CacheDef):
            return self._cache(stmt)
        if isinstance(stmt, n.QueueDef):
            return self._queue(stmt)
        if isinstance(stmt, n.StorageDef):
            return self._storage(stmt)
        if isinstance(stmt, n.NetworkDef):
            return self._network(stmt)
        if isinstance(stmt, n.SecretDef):
            return self._secret(stmt)
        if isinstance(stmt, n.ConfigDef):
            return self._config(stmt)
        if isinstance(stmt, n.PipelineDef):
            return self._pipeline(stmt)
        if isinstance(stmt, n.EnvironmentDef):
            return self._environment(stmt)
        if isinstance(stmt, n.ClusterDef):
            return self._cluster(stmt)
        return str(stmt)

    def _decorators(self, decs) -> str:
        return "\n".join(f"@{d.name}" for d in decs) + "\n" if decs else ""

    def _service(self, s: n.ServiceDef) -> str:
        body: list[str] = []
        if s.image:
            if isinstance(s.image, n.Identifier):
                body.append(f"image: {s.image.name}")
            elif isinstance(s.image, str):
                body.append(f'image: "{s.image}"')
            else:
                body.append(f"image: {self._expr(s.image)}")
        if s.build:
            body.append("build {")
            b = s.build
            if b.context:
                body.append(f"  context: {b.context}")
            if b.dockerfile:
                body.append(f"  dockerfile: {b.dockerfile}")
            body.append("}")
        for p in s.ports:
            if p.host and p.target:
                body.append(f"port {p.host}:{p.target}")
            elif p.target:
                body.append(f"port {p.target}")
        if s.replicas != 1:
            body.append(f"replicas: {s.replicas}")
        if s.env:
            body.append("env {")
            for e in s.env:
                body.append(f"  {e.name}: {self._env_val(e)}")
            body.append("}")
        if s.depends:
            body.append(f"depends: [{self._join(self._str_list(s.depends))}]")
        if s.resources:
            body.append("resources {")
            if s.resources.requests:
                body.append(f"  requests: {self._rmap(s.resources.requests)}")
            if s.resources.limits:
                body.append(f"  limits: {self._rmap(s.resources.limits)}")
            body.append("}")
        if s.health:
            h = s.health
            body.append(f'health {h.kind}("{h.path or "/"}")')
        prefix = self._decorators(s.decorators)
        block = self._render_block("service " + s.name, body)
        return prefix + block

    def _env_val(self, e: n.EnvEntry) -> str:
        if e.value is not None:
            return self._expr(e.value)
        if e.from_secret:
            name, _, key = e.from_secret.partition(".")
            return f'from secret "{name}"{("." + key) if key else ""}'
        if e.from_config:
            name, _, key = e.from_config.partition(".")
            return f'from config "{name}"{("." + key) if key else ""}'
        if e.from_env:
            return f'from env "{e.from_env}"'
        return ""

    @staticmethod
    def _rv(r) -> str:
        if r is None:
            return ""
        return r.to_kubernetes()

    @staticmethod
    def _rmap(m: n.ResourceMap) -> str:
        parts = []
        if m.cpu:
            parts.append(f"cpu: {m.cpu.to_kubernetes()}")
        if m.memory:
            parts.append(f"memory: {m.memory.to_kubernetes()}")
        return "{" + ", ".join(parts) + "}"

    def _database(self, d: n.DatabaseDef) -> str:
        body = [f"type: {d.type}"]
        if d.version:
            body.append(f"version: {d.version}")
        if d.replicas != 1:
            body.append(f"replicas: {d.replicas}")
        if d.ha:
            body.append("ha: true")
        if d.size:
            body.append(f"size: {d.size.to_kubernetes()}")
        if d.backup:
            body.append("backup {")
            if d.backup.enabled:
                body.append("  enabled: true")
            if d.backup.schedule:
                body.append(f'  schedule: "{d.backup.schedule}"')
            body.append("}")
        if d.users:
            body.append("users {")
            for u in d.users:
                body.append("  " + u.name + ": " + (u.password or _Q))
            body.append("}")
        return self._decorators(d.decorators) + self._render_block(
            "database " + d.name, body
        )

    def _cache(self, c: n.CacheDef) -> str:
        body = [f"type: {c.type}"]
        if c.version:
            body.append(f"version: {c.version}")
        if c.maxmemory:
            body.append(f"maxmemory: {c.maxmemory.to_kubernetes()}")
        if c.policy:
            body.append(f"policy: {c.policy}")
        if c.persistence:
            body.append("persistence: true")
        if c.replicas != 1:
            body.append(f"replicas: {c.replicas}")
        return self._decorators(c.decorators) + self._render_block(
            "cache " + c.name, body
        )

    def _queue(self, q: n.QueueDef) -> str:
        body = [f"type: {q.type}"]
        if q.version:
            body.append(f"version: {q.version}")
        if q.topics:
            body.append("topics {")
            for t in q.topics:
                parts = []
                if t.partitions:
                    parts.append(f"partitions: {t.partitions}")
                if t.replication:
                    parts.append(f"replication: {t.replication}")
                body.append(f"  {t.name}: {{ {self._join(parts)} }}")
            body.append("}")
        return self._decorators(q.decorators) + self._render_block(
            "queue " + q.name, body
        )

    def _storage(self, s: n.StorageDef) -> str:
        body = [f"type: {s.type}"]
        if s.bucket:
            body.append(f"bucket: {s.bucket}")
        if s.region:
            body.append(f"region: {s.region}")
        if s.size:
            body.append(f"size: {s.size.to_kubernetes()}")
        return self._decorators(s.decorators) + self._render_block(
            "storage " + s.name, body
        )

    def _network(self, nw: n.NetworkDef) -> str:
        body = []
        if nw.cidr:
            body.append(f"cidr: {_qstr(nw.cidr)}")
        if nw.subnets:
            body.append("subnets {")
            for sn in nw.subnets:
                body.append("  " + sn.name + ": { cidr: " + _qstr(sn.cidr) + " }")
            body.append("}")
        return self._decorators(nw.decorators) + self._render_block(
            "network " + nw.name, body
        )

    def _secret(self, s: n.SecretDef) -> str:
        body = []
        for e in s.entries:
            if e.value is not None:
                body.append(f"{e.name}: {e.value}")
            elif e.from_vault:
                body.append(f'{e.name}: from vault "{e.from_vault}"')
            elif e.from_env:
                body.append(f'{e.name}: from env "{e.from_env}"')
            elif e.from_file:
                body.append(f'{e.name}: from file "{e.from_file}"')
        return self._decorators(s.decorators) + self._render_block(
            "secret " + s.name, body
        )

    def _config(self, c: n.ConfigDef) -> str:
        body = []
        for e in c.entries:
            body.append(f"{e.name}: {self._expr(e.value)}")
        return self._decorators(c.decorators) + self._render_block(
            "config " + c.name, body
        )

    def _pipeline(self, p: n.PipelineDef) -> str:
        body = []
        if p.trigger:
            body.append("trigger {")
            if p.trigger.branches:
                body.append(
                    f"  branches: [{self._join(self._str_list(p.trigger.branches))}]"
                )
            if p.trigger.schedule:
                body.append(f"  schedule: {_qstr(p.trigger.schedule)}")
            if p.trigger.manual:
                body.append("  manual: true")
            body.append("}")
        if p.stages:
            body.append("stages {")
            for st in p.stages:
                body.append(f"  {st.name}: {{")
                if st.runs_on:
                    body.append(f"    runsOn: {st.runs_on}")
                if st.needs:
                    body.append(f"    needs: [{self._join(st.needs)}]")
                if st.steps:
                    body.append("    steps {")
                    for step in st.steps:
                        if step.run is not None:
                            body.append(f'      {step.name}: {{ run: "{step.run}" }}')
                        elif step.uses:
                            body.append(f'      {step.name}: {{ uses: "{step.uses}" }}')
                    body.append("    }")
                body.append("  }")
            body.append("}")
        return self._decorators(p.decorators) + self._render_block(
            "pipeline " + p.name, body
        )

    def _environment(self, e: n.EnvironmentDef) -> str:
        body = []
        if e.provider:
            body.append(f"provider: {e.provider}")
        if e.region:
            body.append(f"region: {e.region}")
        if e.namespace:
            body.append(f"namespace: {e.namespace}")
        return self._decorators(e.decorators) + self._render_block(
            "environment " + e.name, body
        )

    def _cluster(self, c: n.ClusterDef) -> str:
        body = []
        if c.provider:
            body.append(f"provider: {c.provider}")
        if c.region:
            body.append(f"region: {c.region}")
        if c.nodes:
            body.append("nodes {")
            for np_ in c.nodes:
                body.append(
                    "  "
                    + np_.name
                    + ": { machine type: "
                    + _qstr(np_.machine_type)
                    + " min: "
                    + str(np_.min or 1)
                    + " max: "
                    + str(np_.max or 3)
                    + " }"
                )
            body.append("}")
        return self._decorators(c.decorators) + self._render_block(
            "cluster " + c.name, body
        )

    # ------------------------------------------------------------------ #
    def _render_block(self, head: str, body: list[str]) -> str:
        lines = [head + " {"]
        for b in body:
            lines.append(" " * self.indent + b)
        lines.append("}")
        return "\n".join(lines)


def format_source(source: str, indent: int = 4) -> str:
    """Parse *source* and return its formatted representation."""
    program = _parser().parse(source)  # cached parser (avoids rebuilding grammar)
    return InfraPrinter(indent=indent).print(program)


def format_file(path: Path, indent: int = 4):
    """Return (formatted_source, changed: bool)."""
    source = Path(path).read_text(encoding="utf-8")
    formatted = format_source(source, indent)
    return formatted, (formatted != source)
