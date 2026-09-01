"""Behavioral tests: each test documents a business rule using
Given / When / Then phrasing, asserting concrete values rather than
``is not None``. These read like a living specification of the DSL.
"""

from __future__ import annotations

import pytest

from infra import parse, validate
from infra.diff.engine import InfraDiff

pytestmark = pytest.mark.behavioral


# --------------------------------------------------------------------------- #
# Security enforcement
# --------------------------------------------------------------------------- #


class TestSecurityEnforcement:
    def test_hardcoded_password_in_env_is_rejected(self, assert_error):
        """Given a service with ``PASSWORD: "literal"`` in env,
        When it is validated,
        Then a SEC001 error is produced with a hint pointing at ``from secret``."""
        err = assert_error(
            'service api { image: "x:1" env { PASSWORD: "hardcoded123" } }',
            "SEC001",
        )
        assert "PASSWORD" in err.message
        assert "secret" in (err.hint or "").lower()

    def test_mutable_image_tag_produces_warning_not_error(self, assert_warning):
        """Given a service pinned to ``nginx:latest``,
        When it is validated,
        Then a SEC003 warning (not error) is produced, so deployment is not blocked."""
        assert_warning('service api { image: "nginx:latest" }', "SEC003")

    def test_valid_service_passes_all_security_checks(self):
        """Given a well-configured service (private registry, no env secrets),
        When it is validated,
        Then no SEC* errors are produced."""
        result = validate(
            parse(
                'service api { image: "registry.io/api:1.2.3" security { user: 1000 } }'
            )
        )
        assert not [e for e in result.errors if e.code.startswith("SEC")]

    def test_privileged_container_is_rejected(self, assert_error):
        """Given a service with ``privileged: true``,
        When it is validated,
        Then a SEC004 error is produced (full host access is critical)."""
        err = assert_error(
            'service api { image: "x:1" security { privileged: true } }',
            "SEC004",
        )
        assert "privileged" in err.message.lower()


# --------------------------------------------------------------------------- #
# Reliability guidance
# --------------------------------------------------------------------------- #


class TestReliabilityGuidance:
    def test_database_without_backup_gets_warning_with_hint(self, assert_warning):
        """Given a database with no ``backup`` block,
        When it is validated,
        Then a REL006 warning is produced and it carries a hint."""
        w = assert_warning("database db { type: postgres }", "REL006")
        assert w.hint

    def test_ha_database_with_even_replicas_suggests_odd_number(self, assert_warning):
        """Given an HA database with ``replicas: 2``,
        When it is validated,
        Then REL002 suggests an odd replica count (3)."""
        w = assert_warning(
            "database db { type: postgres ha: true replicas: 2 }", "REL002"
        )
        assert w.hint and "3" in w.hint

    def test_service_without_memory_limit_gets_hint(self, assert_warning):
        """Given a service whose resources have no memory limit,
        When it is validated,
        Then REL003 (no memory limit) is reported."""
        assert_warning('service api { image: "x:1" replicas: 5 }', "REL003")


# --------------------------------------------------------------------------- #
# Compilation correctness
# --------------------------------------------------------------------------- #


class TestCompilationCorrectness:
    def test_service_image_appears_unchanged_in_k8s_output(self, k8s_docs):
        """Given a service with ``image: "nginx:1.2.3"``,
        When it is compiled to Kubernetes,
        Then the container image matches exactly (no tag rewriting)."""
        dep = next(
            d
            for d in k8s_docs('service api { image: "nginx:1.2.3" }')
            if d["kind"] == "Deployment"
        )
        image = dep["spec"]["template"]["spec"]["containers"][0]["image"]
        assert image == "nginx:1.2.3"

    def test_replicas_count_matches_exactly_in_deployment(self, k8s_docs):
        """Given ``replicas: 7``,
        When compiled to Kubernetes,
        Then Deployment.spec.replicas == 7."""
        dep = next(
            d
            for d in k8s_docs('service api { image: "x:1" replicas: 7 }')
            if d["kind"] == "Deployment"
        )
        assert dep["spec"]["replicas"] == 7

    def test_const_interpolated_in_template_string(self, k8s_docs):
        """Given ``const VERSION = "v2"`` and an image ```app:{VERSION}```,
        When compiled,
        Then the image becomes ``app:v2``."""
        dep = next(
            d
            for d in k8s_docs(
                'const VERSION = "v2"\nservice api { image: `app:{VERSION}` }'
            )
            if d["kind"] == "Deployment"
        )
        assert dep["spec"]["template"]["spec"]["containers"][0]["image"] == "app:v2"


# --------------------------------------------------------------------------- #
# Diff detection
# --------------------------------------------------------------------------- #


class TestDiffDetection:
    def test_identical_files_report_no_changes(self):
        """Given two identical sources,
        When diffed,
        Then no changes are reported and the program is marked unchanged."""
        r = InfraDiff().diff(
            parse('service api { image: "nginx:1.0" }'),
            parse('service api { image: "nginx:1.0" }'),
        )
        assert not r.has_changes
        assert "api" in r.unchanged

    def test_changed_image_shows_before_and_after_values(self):
        """Given image changes from 1.0 to 2.0,
        When diffed,
        Then the changed field exposes before=1.0 and after=2.0."""
        r = InfraDiff().diff(
            parse('service api { image: "nginx:1.0" }'),
            parse('service api { image: "nginx:2.0" }'),
        )
        img = next(ch for ch in r.changed[0].changes if "image" in ch.field_path)
        assert img.before == "nginx:1.0"
        assert img.after == "nginx:2.0"

    def test_new_service_appears_in_added_section(self):
        """Given a second service is added,
        When diffed,
        Then it appears in the ``added`` section with kind ``service``."""
        r = InfraDiff().diff(
            parse('service api { image: "nginx:1.0" }'),
            parse(
                'service api { image: "nginx:1.0" }\n'
                'service worker { image: "redis:7" }'
            ),
        )
        assert any(i.name == "worker" and i.kind == "service" for i in r.added)


# --------------------------------------------------------------------------- #
# Import system
# --------------------------------------------------------------------------- #


class TestImportSystem:
    def test_imported_const_is_available_in_importing_file(self, infra_file, tmp_path):
        """Given ``base.infra`` defines a const and ``main.infra`` imports it,
        When ``main.infra`` is validated,
        Then validation passes without unresolved-symbol errors."""
        (tmp_path / "base.infra").write_text('const BASE_IMAGE = "alpine:3.18"')
        main = infra_file(
            'import "./base.infra"\nservice api { image: BASE_IMAGE }',
            "main.infra",
        )
        from infra.parser import parse_file

        result = validate(parse_file(main))
        assert result.is_valid, [e.code for e in result.errors]

    def test_circular_import_raises_descriptive_error(self, tmp_path):
        """Given two files importing each other,
        When one is parsed,
        Then an ImportCycleError naming both files is raised."""
        from infra.parser import parse_file

        (tmp_path / "a.infra").write_text(
            'import "./b.infra"\nservice a { image: "x:1" }'
        )
        (tmp_path / "b.infra").write_text(
            'import "./a.infra"\nservice b { image: "y:1" }'
        )
        with pytest.raises(Exception) as exc_info:
            parse_file(tmp_path / "a.infra")
        assert "Circular import" in str(exc_info.value)
