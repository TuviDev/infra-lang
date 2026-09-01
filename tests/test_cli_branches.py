"""Session 10 coverage-boost tests: modules below 88%.

Targets docs.py, symbols.py, compose.py and kubernetes.py branches that were
not previously exercised, each via REAL behavior.
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from infra import parse
from infra.analyzer import types as T  # noqa: N812
from infra.analyzer.symbols import Symbol, SymbolKind, SymbolTable
from infra.backends import get_backend
from infra.cli.main import app

runner = CliRunner()


class TestDocsBranches:
    def test_describe_all_definition_kinds(self, tmp_path):
        f = tmp_path / "docs.infra"
        f.write_text(
            'service svc { image: "x:1" }\n'
            "database db { type: postgres }\n"
            "cache cache1 { type: redis }\n"
            "queue queue1 { type: rabbitmq }\n"
            "storage store1 { type: object }\n"
            'pipeline pipe1 { stages { a: { runsOn: "x" } } }\n'
            'secret sec { key: from env "K" }\n'
            'config cfg { VAL: "x" }\n'
            'network net { cidr: "10.0.0.0/16" }\n'
            'environment env { namespace: "ns" }\n'
            "cluster cl { provider: aws }\n"
        )
        r = runner.invoke(app, ["docs", str(f)])
        assert r.exit_code == 0, r.output
        for needle in (
            "**service**",
            "**database**",
            "**cache**",
            "**queue**",
            "**storage**",
            "**pipeline**",
            "**secret**",
            "**config**",
            "**network**",
            "**environment**",
            "**cluster**",
        ):
            assert needle in r.output


class TestSymbolTableBranches:
    def test_scope_and_symbol_table_methods(self):
        st = SymbolTable()
        st.define(Symbol("x", T.INT, kind=SymbolKind.CONST))
        assert st.lookup("x") is not None
        assert st.lookup("missing") is None
        assert st.lookup_local("x") is not None
        assert st.get_all_definitions()["x"].name == "x"
        # builtins registered at init
        assert len(st.get_all_definitions()) > 1

    def test_nested_scope_lookup(self):
        st = SymbolTable()
        st.define(Symbol("outer", T.STRING))
        with st:
            st.define(Symbol("inner", T.INT))
            assert st.lookup("inner") is not None
            assert st.lookup("outer") is not None  # parent lookup
        # inner symbol no longer visible after exiting scope
        assert st.lookup("inner") is None

    def test_child_scope(self):
        from infra.analyzer.symbols import ScopeKind

        st = SymbolTable()
        child = st.current_scope.child(ScopeKind.BLOCK)
        assert child.parent is st.current_scope
        assert child.lookup_local("anything") is None


class TestComposeBranches:
    def _c(self, src):
        return get_backend("compose").compile(parse(src)).files["docker-compose.yml"]

    def test_service_build_command_env_health(self):
        content = self._c(
            'service api { build { context: "." dockerfile: "Dockerfile" } '
            'command: ["npm", "start"] env { DB: from env "DATABASE_URL" } '
            'health tcp(8080) volumes [ { name: data mountPath: "/data" } ] }'
        )
        assert "build:" in content
        assert "npm" in content
        assert "${DATABASE_URL}" in content
        assert "nc" in content

    def test_database_variants(self):
        content = self._c(
            'database pg { type: postgres users { admin: "pw" } }\n'
            "database my { type: mysql }\n"
            'database mo { type: mongodb users { root: "pw" } }\n'
        )
        assert "POSTGRES_PASSWORD" in content
        assert "MYSQL_DATABASE" in content
        assert "MONGO_INITDB_ROOT_USERNAME" in content

    def test_cache_and_queue(self):
        content = self._c(
            "cache r { type: redis maxmemory: 512Mi persistence: true }\n"
            'queue q { type: rabbitmq users { app: "pw" } }\n'
        )
        assert "redis-server" in content
        assert "RABBITMQ_DEFAULT_USER" in content


class TestKubernetesExtraBranches:
    def _k(self, src):
        return get_backend("kubernetes").compile(parse(src)).files["infra.yaml"]

    def _deployment(self, src):
        docs = [d for d in yaml.safe_load_all(self._k(src)) if d]
        return next(d for d in docs if d["kind"] == "Deployment")

    def test_service_build_command_strategy(self):
        dep = self._deployment(
            'service api { build { context: "." } command: ["node", "start"] '
            "strategy: recreate }"
        )
        container = dep["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "built-from-dockerfile"
        assert container["command"] == ["node", "start"]
        assert dep["spec"]["strategy"] == {"type": "Recreate"}

    def test_env_field_and_security_group(self):
        dep = self._deployment(
            'service api { image: "x:1" '
            'env { POD: from field "metadata.name" } '
            "security { group: 1000 } }"
        )
        container = dep["spec"]["template"]["spec"]["containers"][0]
        field = container["env"][0]["valueFrom"]["fieldRef"]["fieldPath"]
        assert field == "metadata.name"
        assert container["securityContext"] == {"runAsGroup": 1000}

    def test_secret_from_vault(self):
        content = self._k('secret sec { a: from vault "secret/a" }\n')
        docs = [d for d in yaml.safe_load_all(content) if d]
        secret = next(d for d in docs if d["kind"] == "Secret")
        # Values in data: are base64; the placeholder must decode back.
        import base64

        assert base64.b64decode(secret["data"]["a"]).decode() == "from-vault:secret/a"


class TestLspCmdCoverage:
    def test_lsp_stdio_mode(self, monkeypatch):
        from typer.testing import CliRunner

        import infra.lsp.server as s
        from infra.cli.main import app

        called = {}
        monkeypatch.setattr(s.server, "start_io", lambda: called.setdefault("io", True))
        r = CliRunner().invoke(app, ["lsp"])
        assert r.exit_code == 0
        assert called.get("io") is True

    def test_lsp_tcp_mode(self, monkeypatch):
        from typer.testing import CliRunner

        import infra.lsp.server as s
        from infra.cli.main import app

        called = {}
        monkeypatch.setattr(
            s.server, "start_tcp", lambda h, p: called.update({"tcp": (h, p)})
        )
        r = CliRunner().invoke(app, ["lsp", "--tcp", "--port", "2088"])
        assert r.exit_code == 0
        assert called.get("tcp") == ("127.0.0.1", 2088)


class TestFeedbackCmdCoverage:
    def test_on_and_off_conflict_errors(self):
        from typer.testing import CliRunner

        from infra.cli.main import app

        r = CliRunner().invoke(app, ["feedback", "--on", "--off"])
        assert r.exit_code == 1
        assert "only one of --on / --off" in r.output

    def test_user_config_path_fallback(self, tmp_path, monkeypatch):
        import infra.config as cfg
        from infra.cli.feedback_cmd import _project_config_path

        monkeypatch.setattr(cfg, "USER_CONFIG_PATH", tmp_path / "user.yaml")
        monkeypatch.chdir(tmp_path)  # no project config present
        assert _project_config_path() == tmp_path / "user.yaml"


class TestFmtCoverage:
    def test_fmt_diff_flag(self, tmp_path):
        from typer.testing import CliRunner

        from infra.cli.main import app

        f = tmp_path / "t.infra"
        f.write_text('service s {\nimage:"x"\n}')
        r = CliRunner().invoke(app, ["fmt", str(f), "--diff"])
        assert r.exit_code == 0
        assert "-" in r.output or "+" in r.output or "diff" in r.output.lower()
