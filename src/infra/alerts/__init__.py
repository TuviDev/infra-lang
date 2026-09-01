"""Alerting & webhooks for infra-lang (v0.7.0).

Slack / Microsoft Teams / Discord notifications for drift, budget overruns
and security violations — stdlib ``urllib`` only, so it works everywhere
(including minimal CI images) without extra dependencies.
"""

from infra.alerts.webhooks import (
    ALL_EVENTS,
    DEFAULT_TIMEOUT,
    EVENT_COST_EXCEEDED,
    EVENT_DRIFT,
    EVENT_SECURITY,
    FORMATS,
    AlertConfig,
    AlertConfigError,
    AlertContext,
    AlertEvent,
    WebhookTarget,
    build_payload,
    evaluate_alerts,
    load_alert_config,
    mask_url,
    post_webhook,
)

__all__ = [
    "ALL_EVENTS",
    "DEFAULT_TIMEOUT",
    "EVENT_COST_EXCEEDED",
    "EVENT_DRIFT",
    "EVENT_SECURITY",
    "FORMATS",
    "AlertConfig",
    "AlertConfigError",
    "AlertContext",
    "AlertEvent",
    "WebhookTarget",
    "build_payload",
    "evaluate_alerts",
    "load_alert_config",
    "mask_url",
    "post_webhook",
]
