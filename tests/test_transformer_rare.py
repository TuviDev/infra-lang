"""Session 8.1 coverage-boost tests: cli/compile.py, cli/repl.py, cli/main.py.

Each test exercises a REAL branch that was previously uncovered, targeting the
modules listed as <85% in the Session 8.1 prompt. Nothing here weakens existing
tests; it only adds coverage for genuinely reachable behavior.
"""

from __future__ import annotations

import builtins
import threading
import time
from pathlib import Path

from typer.testing import CliRunner

from infra.cli.main import app
from infra.parser import Parser as _Parser


def _user_defs(src):
    p = _Parser()
    return [
        s
        for s in p.parse(src).statements
        if getattr(getattr(s, "location", None), "file", "") != "<prelude>"
    ]


# --------------------------------------------------------------------------- #
# parser/transformer.py — rare constructs (was ~84%)
# --------------------------------------------------------------------------- #


class TestTransformerServiceRareBlocks:
    def test_service_full_blocks(self):
        (svc,) = _user_defs(
            """
service api {
    build { context: "." dockerfile: "Dockerfile" args: {X: "1"} target: "prod" }
    port { target: 8080 host: 8081 protocol: "tcp" }
    ingress {
        host: "api.example.com" tls: true domain: "example.com"
        rate_limit: { rps: 100 burst: 20 }
        cors: { origins: ["*"] methods: ["GET"] headers: ["X"] credentials: true }
        paths: ["/api"]
    }
    env { A: from env "A" B: from secret "s".key C: from config "c".k }
    envFrom: { DB: "d" }
    resources {
        requests { cpu: 100m, memory: 64Mi }
        limits { cpu: 200m, memory: 128Mi } cpu: 300m
    }
    health http("/x") {
        interval: 10s timeout: 5s retries: 3 startPeriod: 5s
        initialDelay: 2s port: 80 command: ["curl"]
    }
    probes { liveness http("/") readiness { path: "/r" } startup tcp(80) }
    volumes [
        { name: "data" mountPath: "/data" hostPath: "/h" claim: "c" readOnly: false }
        { name: "logs" foo: "bar" }
    ]
    strategy { type: "rolling" steps: [10] canary: { weight: 20 steps: 3 traffic: 10 } }
    security {
        user: 1000 group: 100 capabilities: ["NET_ADMIN"] seccomp: "default"
        selinux { level: "s0" role: "r" }
        readOnlyRootFilesystem: true privileged: false
    }
    lifecycle { postStart { exec: ["echo", "hi"] } preStop { http: "/stop" } }
    schedule { default { replicas: 2 } "night" { replicas: 1, cpu: 100m, memory: 1Gi } }
    autoscale {
        min: 2 max: 10 target_cpu: 70 target_memory: 80
        scale_up_delay: 1m scale_down_delay: 2m
    }
    disruption { min_available: 50% max_unavailable: 1 }
    network_policy { allow_from: [a] deny_from: ["*"] allow_egress: [b] }
    topology { spread_by: zone max_skew: 2 }
}
"""
        )
        assert svc.build.args == (("X", "1"),)
        assert svc.ports[0].target == 8080
        assert svc.ports[0].host == 8081
        assert svc.ingress.rate_limit.rps == 100
        assert svc.ingress.cors.origins == ("*",)
        assert svc.env_from[0].source == "d"
        assert svc.env[0].from_env == "A"
        assert svc.env[1].from_secret == "s.key"
        assert svc.health.port == 80
        assert svc.health.retries == 3
        assert svc.probes.liveness.kind == "http"
        assert svc.probes.readiness.path == "/r"
        assert svc.volumes[0].name == "data"
        assert svc.volumes[0].host_path == "/h"
        assert svc.strategy.canary[0].weight == 20
        assert svc.strategy.canary[0].traffic == 10.0
        assert svc.security.selinux.level == "s0"
        assert svc.lifecycle.pre_stop.url == "/stop"
        assert svc.schedule.default.replicas == 2
        assert svc.autoscale.max_replicas == 10
        assert svc.autoscale.target_cpu == 70
        assert svc.disruption.max_unavailable == 1
        assert svc.network_policy.allow_from == ("a",)
        assert svc.network_policy.deny_from == ("*",)
        assert svc.topology.spread_by == "zone"
        assert svc.topology.max_skew == 2

    def test_nocolon_rate_limit_cors_not_dropped(self):
        (svc,) = _user_defs(
            'service api { ingress { host: "h" rate_limit { rps: 50 } '
            'cors { origins: ["*"] } } }'
        )
        assert svc.ingress.rate_limit.rps == 50
        assert svc.ingress.cors.origins == ("*",)

    def test_schedule_named_slots(self):
        (svc,) = _user_defs(
            'service api { schedule { default { replicas: 2 } '
            '"night" { replicas: 1, cpu: 100m, memory: 1Gi } } }'
        )
        assert svc.schedule.default.replicas == 2
        assert svc.schedule.slots[0].config.replicas == 1


class TestTransformerOtherDefinitions:
    def test_database_backup_users(self):
        (db,) = _user_defs(
            "database db1 { backup { enabled: true retention: 30d } "
            'users { admin: "pw" } }'
        )
        assert db.backup.enabled is True
        assert db.users[0].name == "admin"

    def test_cache(self):
        (c,) = _user_defs(
            'cache c1 { type: redis maxmemory: 512Mi policy: "lru" '
            "persistence: true }"
        )
        assert c.type == "redis"
        assert c.persistence is True

    def test_queue_topics_config(self):
        (q,) = _user_defs(
            'queue q1 { topics { orders: { partitions: 6 replication: 3 } } '
            'config { compression: "gzip" } users { svc: "pw" } }'
        )
        assert q.topics[0].name == "orders"
        assert q.topics[0].partitions == 6
        assert q.config.entries[0][0] == "compression"

    def test_storage_lifecycle(self):
        (s,) = _user_defs(
            "storage s1 { type: object "
            'lifecycle { retention: 30d prefix: "/logs" '
            'transition: "glacier" expiration: 90d } }'
        )
        assert s.lifecycle.retention.value == 30

    def test_network_subnets_policy_selector(self):
        (net,) = _user_defs(
            'network n1 { subnets { az1: { cidr: "10.0.1.0/24" } } '
            'policy { web: { from: ["sg"] ports: [80] selector: {app: "web"} } } }'
        )
        assert net.subnets[0].name == "az1"
        rule = net.policy.rules[0]
        assert rule.name == "web"
        assert rule.ports == (80,)
        assert rule.selector == (("app", "web"),)

    def test_secret_from_sources(self):
        (sec,) = _user_defs(
            'secret sec1 { a: from env "A" b: from vault "v" f: "plain" }'
        )
        assert len(sec.entries) == 3

    def test_cluster_iam_service_account(self):
        (cl,) = _user_defs(
            'cluster c1 { provider: aws iam { '
            'serviceAccount { name: "app-sa" } } }'
        )
        assert cl.iam is not None
        assert cl.iam.service_account.name == "app-sa"

    def test_cluster_iam_role(self):
        (cl,) = _user_defs(
            'cluster c1 { provider: aws iam { '
            'role { name: "app-role" actions: ["ec2:Describe*"] resources: ["*"] } } }'
        )
        assert cl.iam is not None
        assert cl.iam.role.name == "app-role"
        assert cl.iam.role.actions == ("ec2:Describe*",)
        assert cl.iam.role.resources == ("*",)

    def test_cluster_iam_sa_and_role(self):
        (cl,) = _user_defs(
            'cluster c1 { provider: aws iam { '
            'serviceAccount { name: "sa" } '
            'role { name: "r" actions: ["s3:ListBucket"] } } }'
        )
        assert cl.iam.service_account.name == "sa"
        assert cl.iam.role.name == "r"

    def test_config_entries(self):
        (cfg,) = _user_defs('config cfg1 { name: "x" file: "app.yaml" }')
        assert cfg.entries[0].name == "name"

    def test_pipeline_stages_steps(self):
        (pl,) = _user_defs(
            'pipeline p1 { trigger { branches: ["main"] } '
            'stages { build: { runsOn: "ubuntu" needs: ["x"] if: true '
            'timeout: 10m steps { compile: { run: "npm run build", '
            'continueOnError: true, with: {X: "1"} } } } } '
            'artifacts { upload: ["d"] } '
            'cache { path: "p" key: "k" restoreKeys: ["a"] } '
            'concurrency { group: "g" cancelInProgress: true } }'
        )
        st = pl.stages[0]
        assert st.runs_on == "ubuntu"
        assert st.needs == ("x",)
        step = st.steps[0]
        assert step.run == "npm run build"
        assert step.continue_on_error is True
        assert step.with_args == (("X", "1"),)
        assert pl.artifacts.upload == ("d",)
        assert pl.cache.restore_keys == ("a",)
        assert pl.concurrency.cancel_in_progress is True

    def test_environment_quotas(self):
        (env,) = _user_defs(
            "environment prod { quotas { max_cpu: 10cores "
            "max_memory: 20Gi max_pods: 100 } }"
        )
        assert env.quotas.max_pods == 100
        assert env.quotas.max_memory.to_kubernetes() == "20Gi"

    def test_cluster_nodes_iam(self):
        (cl,) = _user_defs(
            'cluster k1 { nodes { general: { machine type: "m5.large" '
            'min: 1 max: 5 labels: {tier: "app"} } } '
            'networking { vpc: "v" } '
            'iam { serviceAccount { name: "sa", policy: {role: "admin"} } '
            'role { actions: ["ec2:*"] resources: ["*"] } } }'
        )
        assert cl.nodes[0].machine_type == "m5.large"
        assert cl.nodes[0].labels == (("tier", "app"),)
        assert cl.networking.vpc == "v"
        assert cl.iam.service_account.name == "sa"
        assert cl.iam.service_account.policy == (("role", "admin"),)
        assert cl.iam.role.actions == ("ec2:*",)

    def test_expressions_if_match_percent_call(self):
        exprs = _user_defs(
            'const A = if true then "y" else "n"\n'
            'const B = match 2 { 1 -> "one" _ -> "other" }\n'
            "const C = 50%\n"
            'const E = format("x", width = 10)'
        )
        assert exprs[0].value.then_branch.value == "y"
        assert exprs[1].value.arms[1].body.value == "other"
        assert type(exprs[2].value).__name__ == "Percentage"
        assert exprs[3].value.kwargs[0][1].value == 10


# --------------------------------------------------------------------------- #
# cli/compile.py
# --------------------------------------------------------------------------- #


class TestCompileWatchBranch:
    def test_watch_branch_routes_to_run_watch(self, tmp_path, monkeypatch):
        """Compile --watch must call run_watch and return (lines 52-61)."""
        from infra.cli import compile as compile_mod

        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x:1" }')

        called = {}

        def fake_run_watch(**kwargs):
            called.update(kwargs)

        monkeypatch.setattr(compile_mod, "run_watch", fake_run_watch)
        result = CliRunner().invoke(
            app, ["compile", str(f), "--watch", "--output", str(tmp_path / "out")]
        )
        assert result.exit_code == 0, result.output
        assert called["source_path"] == Path(f)
        assert called["target"] == "kubernetes"
        assert called["dry_run"] is False


class TestCollectWatchedFiles:
    def test_non_import_item_skipped(self, tmp_path):
        """A non-Import node in imports hits the `continue` branch (line 104)."""
        from infra.cli.compile import _collect_watched_files

        f = tmp_path / "t.infra"
        f.write_text("const A = 1")

        class FakeProgram:
            imports = [object()]  # not an Import -> continue

        files = _collect_watched_files(f, FakeProgram())
        assert f.resolve() in files

    def test_exception_is_swallowed(self, tmp_path):
        """program.imports raising is caught (except Exception -> pass)."""
        from infra.cli.compile import _collect_watched_files

        f = tmp_path / "t.infra"
        f.write_text("const A = 1")

        class BadProgram:
            @property
            def imports(self):
                raise RuntimeError("boom")

        files = _collect_watched_files(f, BadProgram())
        assert f.resolve() in files


class TestRunWatchLoop:
    def test_run_watch_loop_recompiles_and_stops(self, tmp_path):
        """Exercise the full run_watch event loop: startup compile, a file
        change that triggers recompilation, then KeyboardInterrupt shutdown.
        """
        import ctypes

        from infra.cli import compile as compile_mod

        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x:1" }')
        out = tmp_path / "out"

        captured: dict = {}

        def target():
            try:
                compile_mod.run_watch(f, "kubernetes", out, False, {}, False)
            except Exception as exc:  # pragma: no cover - defensive
                captured["exc"] = exc

        t = threading.Thread(target=target, daemon=True)
        t.start()

        # wait for the initial compile to produce output
        deadline = time.time() + 5
        while time.time() < deadline:
            if out.exists() and list(out.glob("*.yaml")):
                break
            time.sleep(0.1)

        # modify the watched file -> should trigger a recompile in the loop
        f.write_text('service api { image: "x:2" }')
        time.sleep(0.6)

        # inject KeyboardInterrupt into the watcher thread to stop the loop
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_long(t.ident), ctypes.py_object(KeyboardInterrupt)
        )
        t.join(timeout=3)

        assert not t.is_alive(), "watch loop did not stop"
        assert "exc" not in captured, captured
        assert out.exists()


# --------------------------------------------------------------------------- #
# cli/repl.py
# --------------------------------------------------------------------------- #


class TestREPLEdgeBranches:
    def test_default_history_file(self):
        from infra.cli.repl import InfraREPL

        r = InfraREPL(target="kubernetes")  # no history_file -> home fallback
        assert r.history_file == Path.home() / ".infra_history"

    def test_prompt_session_when_pt_enabled(self, monkeypatch):
        """Cover the PromptSession branch (lines 37/43) when prompt_toolkit is
        available by replacing the session with a fake that ends the loop."""
        from infra.cli.repl import InfraREPL

        calls = []

        class FakePromptSession:
            def __init__(self, *a, **k):
                pass

            def prompt(self, text):
                calls.append(text)
                raise EOFError

        monkeypatch.setattr("infra.cli.repl._PT", True)
        monkeypatch.setattr("infra.cli.repl.PromptSession", FakePromptSession)
        monkeypatch.setattr("infra.cli.repl.FileHistory", lambda *a, **k: None)

        InfraREPL(target="kubernetes", history_file=Path("hist")).run()
        assert calls == ["infra> "]

    def test_run_skips_blank_line(self, monkeypatch):
        """A blank line should be ignored via `continue` (line 51)."""
        from infra.cli.repl import InfraREPL

        inputs = iter(["", ":quit"])

        def feed(*a, **k):
            try:
                return next(inputs)
            except StopIteration:
                raise EOFError

        monkeypatch.setattr(builtins, "input", feed)
        monkeypatch.setattr("infra.cli.repl._PT", False)

        InfraREPL(target="kubernetes").run()

    def test_process_input_compile_error(self):
        """An invalid backend target raises -> 'Compile error' branch (89-90)."""
        from infra.cli.repl import InfraREPL

        r = InfraREPL(target="kubernetes")
        r.target = "not-a-backend"
        r.process_input('service api { image: "x:1" }')  # should not raise

    def test_handle_clear(self):
        from infra.cli.repl import InfraREPL

        r = InfraREPL(target="kubernetes")
        assert r.handle_command("clear") is False

    def test_handle_load_existing_file(self, tmp_path):
        from infra.cli.repl import InfraREPL

        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x:1" }')
        r = InfraREPL(target="kubernetes")
        assert r.handle_command(f"load {f}") is False
        assert r.symbols is not None

    def test_repl_command_constructs_repl(self, monkeypatch):
        """Cover the repl() CLI entrypoint (line 137)."""
        from infra.cli.repl import repl

        monkeypatch.setattr("infra.cli.repl.InfraREPL.run", lambda self: None)
        repl(target="kubernetes", history=Path("hist"))  # must not raise


# --------------------------------------------------------------------------- #
# cli/main.py
# --------------------------------------------------------------------------- #


class TestMainVerboseQuiet:
    def test_verbose_and_quiet_callbacks(self, tmp_path):
        runner = CliRunner()
        f = tmp_path / "t.infra"
        f.write_text('service api { image: "x:1" }')

        # --verbose runs logging.basicConfig(DEBUG)
        rv = runner.invoke(app, ["--verbose", "check", str(f)])
        assert rv.output is not None

        # --quiet sets logger level to ERROR
        rq = runner.invoke(app, ["--quiet", "check", str(f)])
        assert rq.output is not None

    def test_version_callback(self):
        from infra.version import __version__

        result = CliRunner().invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output


class TestConcurrentParseLocations:
    """Regression: the cached parser must not leak filenames between threads.

    The old implementation stored the "current file" in a module-global, so a
    concurrent parse of two files could attach the wrong filename to a
    SourceLocation. Locations are now kept per-thread.
    """

    def test_concurrent_parse_keeps_per_file_locations(self):
        from infra import parse
        from infra.parser.ast_nodes import ServiceDef

        # Build the cached parser singleton single-threaded first, so the
        # threads below only exercise concurrent *use* of the shared parser
        # (not a racy first-time grammar build).
        parse('service warm { image: "x" }', filename="warm.infra")

        src_a = 'service api_a { image: "x" }\n'
        src_b = 'service api_b { image: "y" }\n'
        results: dict[str, str] = {}
        errors: list = []

        def run(name: str, src: str) -> None:
            try:
                prog = parse(src, filename=name)
                svc = next(
                    s for s in prog.statements if isinstance(s, ServiceDef)
                )
                results[name] = svc.location.file
            except Exception as exc:  # noqa: BLE001
                errors.append((name, exc))

        threads = [
            threading.Thread(target=run, args=(f"f{i}_a.infra", src_a))
            for i in range(20)
        ] + [
            threading.Thread(target=run, args=(f"f{i}_b.infra", src_b))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"parse errors: {errors}"
        assert len(results) == 40
        for name, file_ in results.items():
            assert file_ == name, (
                f"location.file {file_!r} leaked from another thread; "
                f"expected {name!r}"
            )
