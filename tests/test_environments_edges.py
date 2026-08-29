"""Edge overlay tests for infra.analyzer.environments (v0.5.3).

Covers `_apply_service_overlay` branches not exercised before: image,
command, args, annotations, resources and expose overrides.
"""

from __future__ import annotations

from infra.analyzer.environments import apply_environment_overlay
from infra.parser import parse

_BASE_WITH_OVERLAY = """service api {
  image: "app:1"
  env { MODE: "base" KEEP: "1" }
  labels: { team: "core" }
}
environment "staging" {
  service api {
    replicas: 5,
    image: "app:2",
    command: "run-fast",
    args: ["--verbose", "--debug"],
    env { MODE: "overlay" },
    labels: { tier: "gold" },
    annotations: { note: "stg" },
    resources { requests { cpu: 100m, memory: 128Mi } },
    expose: true
  }
}
"""


def _staged_api():
    out = apply_environment_overlay(parse(_BASE_WITH_OVERLAY), "staging")
    return out, next(s for s in out.statements if getattr(s, "name", "") == "api")


class TestServiceOverlayFullMerge:
    def test_scalar_overrides(self) -> None:
        _, api = _staged_api()
        assert api.replicas == 5
        assert api.image == "app:2"
        assert api.command == "run-fast"

    def test_args_override(self) -> None:
        _, api = _staged_api()
        assert len(api.args) == 2

    def test_env_merge_overlay_wins_keeps_base(self) -> None:
        _, api = _staged_api()
        env = {e.name: getattr(e.value, "value", e.value) for e in api.env}
        assert env["MODE"] == "overlay"  # overlay replaces base
        assert env["KEEP"] == "1"  # untouched base entry survives

    def test_labels_merge(self) -> None:
        _, api = _staged_api()
        labels = dict(api.labels)
        assert labels["team"] == "core"
        assert labels["tier"] == "gold"

    def test_annotations_replaced(self) -> None:
        _, api = _staged_api()
        assert dict(api.annotations) == {"note": "stg"}

    def test_resources_override(self) -> None:
        _, api = _staged_api()
        assert api.resources is not None
        assert api.resources.requests is not None
        assert api.resources.requests.cpu is not None
        assert api.resources.requests.cpu.to_kubernetes() == "100m"

    def test_expose_turned_on(self) -> None:
        _, api = _staged_api()
        assert api.expose is True

    def test_overlay_list_consumed(self) -> None:
        out, _ = _staged_api()
        assert out.environments == ()
