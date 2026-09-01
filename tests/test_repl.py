"""Interactive REPL and watch-mode helper branches."""

from __future__ import annotations

from pathlib import Path

from infra.cli.repl import InfraREPL


class TestREPL:
    def make_repl(self, tmp_path=None):
        history = None
        if tmp_path:
            history = Path(tmp_path) / "hist"
        return InfraREPL(target="kubernetes", history_file=history)

    def test_init(self, tmp_path):
        r = self.make_repl(tmp_path)
        assert r.target == "kubernetes"
        assert r.accumulator == []
        assert r.last_ast is None

    def test_is_incomplete_true(self):
        r = self.make_repl()
        assert r._is_incomplete("service a {") is True

    def test_is_incomplete_false(self):
        r = self.make_repl()
        assert r._is_incomplete("service a { image: 'x' }") is False

    def test_handle_quit(self):
        r = self.make_repl()
        assert r.handle_command("quit") is True

    def test_handle_exit(self):
        r = self.make_repl()
        assert r.handle_command("exit") is True

    def test_handle_help(self):
        r = self.make_repl()
        assert r.handle_command("help") is False

    def test_handle_reset(self):
        r = self.make_repl()
        r.accumulator = ["partial"]
        r.handle_command("reset")
        assert r.accumulator == []

    def test_handle_target(self):
        r = self.make_repl()
        r.handle_command("target compose")
        assert r.target == "compose"

    def test_handle_load_missing_file(self):
        r = self.make_repl()
        r.handle_command("load /nonexistent/file.infra")

    def test_handle_show_unknown(self):
        r = self.make_repl()
        r.handle_command("show nope")

    def test_show_symbols_none(self):
        r = self.make_repl()
        r._show("symbols")

    def test_process_input_valid(self):
        r = self.make_repl()
        r.process_input('service api { image: "nginx:1.0" }')
        assert r.symbols is not None

    def test_process_input_invalid(self):
        r = self.make_repl()
        r.process_input('service api { image: "nginx:1.0" replicas: 0 }')

    def test_process_input_parse_error(self):
        r = self.make_repl()
        r.process_input("service {")

    def test_process_input_compile_error(self):
        r = self.make_repl()
        # valid infra but with unsupported content for backend should still
        # not crash; target a backend that succeeds on a service
        r.process_input("cache c { type: redis }")

    def test_show_ast_last(self):
        r = self.make_repl()
        r.last_ast = "SOME_AST"
        r._show("ast")

    def test_show_symbols_with_result(self):
        from infra import parse, validate

        r = self.make_repl()
        r.symbols = validate(parse('service api { image: "x:1" }'))
        r._show("symbols")


class TestREPLCommands:
    def test_run_via_input(self, monkeypatch, tmp_path):
        r = InfraREPL(history_file=Path(tmp_path) / "hist")
        import builtins

        def feed(*a, **k):
            raise EOFError

        monkeypatch.setattr(builtins, "input", feed)
        monkeypatch.setattr("infra.cli.repl._PT", False)
        r.run()  # should terminate on EOFError without crashing

    def test_run_processes_lines(self, monkeypatch, tmp_path):
        r = InfraREPL(history_file=Path(tmp_path) / "hist")
        import builtins

        inputs = iter(
            [
                'service api { image: "nginx:1.0" }',  # definition -> process_input
                "let x = 1",  # variable -> process_input
                ":help",  # command
                "service api {",  # incomplete -> accumulate
                'image: "nginx:2.0" }',  # completes the block
            ]
        )

        def feed(*a, **k):
            try:
                return next(inputs)
            except StopIteration:
                raise EOFError

        monkeypatch.setattr(builtins, "input", feed)
        monkeypatch.setattr("infra.cli.repl._PT", False)
        r.run()
        assert r.accumulator == []

    def test_run_processes_quit(self, monkeypatch, tmp_path):
        r = InfraREPL(history_file=Path(tmp_path) / "hist")
        import builtins

        inputs = iter([":quit"])

        def feed(*a, **k):
            try:
                return next(inputs)
            except StopIteration:
                raise EOFError

        monkeypatch.setattr(builtins, "input", feed)
        monkeypatch.setattr("infra.cli.repl._PT", False)
        r.run()
