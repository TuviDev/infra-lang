"""Every validation error/warning code and message must be produced correctly."""

from __future__ import annotations

from infra import parse, validate


def v(source: str):
    return validate(parse(source))


def error_codes(source: str) -> list:
    return [e.code for e in v(source).errors]


def warn_codes(source: str) -> list:
    return [w.code for w in v(source).warnings]


class TestErrorCodes:
    def test_e001_undefined_variable(self):
        r = v("service api { image: missing_var }")
        e = next(e for e in r.errors if e.code == "E001")
        assert "missing_var" in e.message

    def test_e001_duplicate_variable(self):
        assert "E001" in error_codes("let x = 1\nlet x = 2")

    def test_e002_duplicate_global(self):
        r = v('service api { image: "a" }\nservice api { image: "b" }')
        assert "E002" in [e.code for e in r.errors]

    def test_e010_no_image_or_build(self):
        assert "E010" in error_codes("service api { replicas: 2 }")

    def test_e011_replicas_zero(self):
        assert "E011" in error_codes('service api { image: "a" replicas: 0 }')

    def test_e012_port_out_of_range(self):
        assert "E012" in error_codes('service api { image: "a" port 70000 }')

    def test_e013_duplicate_port(self):
        assert "E013" in error_codes('service api { image: "a" port 80 port 80 }')

    def test_e014_duplicate_env(self):
        assert "E014" in error_codes('service api { image: "a" env { A: "1" A: "2" } }')

    def test_e020_unknown_db_type(self):
        r = v("database db { type: oracle }")
        assert "E020" in [e.code for e in r.errors]

    def test_e020_typo_suggests(self):
        r = v("database db { type: postgress }")
        e = next(e for e in r.errors if e.code == "E020")
        assert e.hint and "postgres" in e.hint

    def test_e021_db_replicas_zero(self):
        assert "E021" in error_codes("database db { type: postgres replicas: 0 }")

    def test_e022_invalid_backup_cron(self):
        assert "E022" in error_codes(
            'database db { type: postgres backup { schedule: "bad" } }'
        )

    def test_e023_duplicate_db_user(self):
        assert "E023" in error_codes(
            "database db { type: postgres users { a: '1' a: '2' } }"
        )

    def test_e024_unknown_cache(self):
        assert "E024" in error_codes("cache c { type: banana }")

    def test_e025_unknown_queue(self):
        assert "E025" in error_codes("queue q { type: banana }")

    def test_e026_unknown_storage(self):
        assert "E026" in error_codes("storage s { type: banana }")

    def test_e027_duplicate_secret_key(self):
        assert "E027" in error_codes('secret s { a: "1" a: "2" }')

    def test_e030_stage_undefined_dep(self):
        r = v(
            'pipeline p { stages { a: { needs: ["nope"] steps { s: { run: "x" } } } } }'
        )
        assert "E030" in [e.code for e in r.errors]

    def test_e031_cyclic_pipeline(self):
        src = (
            'pipeline p { stages { a: { needs: ["b"] steps { s: { run: "1" } } } b: { '
            'needs: ["a"] steps { s: { run: "2" } } } } }'
        )
        assert "E031" in error_codes(src)

    def test_e032_invalid_pipeline_cron(self):
        assert "E032" in error_codes(
            'pipeline p { trigger { schedule: "nope" } stages { a: { steps { s: { run: '
            '"x" } } } } }'
        )

    def test_e033_unknown_provider(self):
        assert "E033" in error_codes("cluster c { provider: banana }")


class TestWarningCodes:
    def test_w001_depends_undefined(self):
        assert "W001" in warn_codes('service api { image: "a" depends: ["db"] }')

    def test_w002_rolling_replicas1(self):
        assert "W002" in warn_codes(
            'service api { image: "a" replicas: 1 strategy: rolling }'
        )

    def test_w003_unused_variable(self):
        assert "W003" in warn_codes('let unused = 1\nservice api { image: "a" }')

    def test_w004_duplicate_import(self):
        assert "W004" in warn_codes('import "./a.infra"\nimport "./a.infra"')

    def test_schedule_invalid_cron_e010(self):
        r = v('service api { image: "a" schedule { "0 9 * *": replicas 3 } }')
        assert "E010" in [e.code for e in r.errors]

    def test_schedule_zero_replicas_e011(self):
        r = v('service api { image: "a" schedule { "0 9 * * 1-5": replicas 0 } }')
        assert "E011" in [e.code for e in r.errors]


class TestSecurityMessages:
    def test_sec001_message(self):
        r = v('service api { image: "nginx:1.0" env { PASSWORD: "bad" } }')
        e = next(e for e in r.errors if e.code == "SEC001")
        assert "PASSWORD" in e.message

    def test_sec003_message(self):
        r = v('service api { image: "nginx:latest" }')
        w = next(w for w in r.warnings if w.code == "SEC003")
        assert "latest" in w.message

    def test_sec004_message(self):
        r = v('service api { image: "nginx:1.0" security { privileged: true } }')
        e = next(e for e in r.errors if e.code == "SEC004")
        assert "privileged" in e.message

    def test_sec006_message(self):
        r = v("database db { type: postgres ssl: false }")
        w = next(w for w in r.warnings if w.code == "SEC006")
        assert "SSL" in w.message

    def test_sec007_message(self):
        r = v('secret s { key: "supersecretpass123" }')
        e = next(e for e in r.errors if e.code == "SEC007")
        assert "hardcoded" in e.message.lower()


class TestReliabilityMessages:
    def test_rel001_message(self):
        r = v('service api { image: "nginx:1.0" replicas: 5 }')
        w = next(w for w in r.warnings if w.code == "REL001")
        assert "startup probe" in w.message

    def test_rel002_message(self):
        r = v("database db { type: postgres replicas: 2 ha: true }")
        w = next(w for w in r.warnings if w.code == "REL002")
        assert "even" in w.message

    def test_rel003_message(self):
        r = v('service api { image: "nginx:1.0" }')
        w = next(w for w in r.warnings if w.code == "REL003")
        assert "memory limit" in w.message

    def test_rel004_message(self):
        r = v('service api { image: "nginx:1.0" }')
        w = next(w for w in r.warnings if w.code == "REL004")
        assert "health" in w.message

    def test_rel006_message(self):
        r = v("database db { type: postgres }")
        w = next(w for w in r.warnings if w.code == "REL006")
        assert "backup" in w.message

    def test_all_rel_have_hints(self):
        r = v(
            'service api { image: "nginx:1.0" replicas: 5 }\ndatabase db { type: '
            'postgres replicas: 2 ha: true }'
        )
        for w in r.warnings:
            if w.code and w.code.startswith("REL"):
                assert w.hint
