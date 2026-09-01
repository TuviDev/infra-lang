"""Webhook payloads and delivery for `infra alert` (Slack/Teams/Discord).

Everything here is stdlib-only (``urllib.request``) so the alerting path
works in minimal CI images. Delivery is a plain HTTP POST with a timeout;
webhook URLs are treated as secrets and never logged in full — use
:func:`mask_url` for display.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from infra.analyzer.cost import estimate_cost
from infra.analyzer.security import SecurityChecker
from infra.errors.exceptions import ValidationError
from infra.parser import ast_nodes as n
from infra.version import __version__

#: Alert event types understood by `infra alert` and ``.infra-alert.yml``.
EVENT_DRIFT = "drift"
EVENT_COST_EXCEEDED = "cost_exceeded"
EVENT_SECURITY = "security_violation"
ALL_EVENTS = (EVENT_DRIFT, EVENT_COST_EXCEEDED, EVENT_SECURITY)

#: Supported webhook payload formats.
FORMATS = ("slack", "teams", "discord")

#: Default HTTP timeout in seconds — a dead webhook must never hang a CI job.
DEFAULT_TIMEOUT = 10.0

#: Discord embed colour for triggered alerts (orange-red).
_COLOR = 0xD63301


class AlertConfigError(Exception):
    """Raised when an ``.infra-alert.yml`` config file is invalid."""


@dataclass
class AlertEvent:
    """A single alertable condition detected for a .infra file."""

    event_type: str
    title: str
    lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.event_type, "title": self.title, "lines": self.lines}


@dataclass
class AlertContext:
    """Everything a notifier knows about the evaluated file."""

    source: str
    monthly_usd: float
    max_monthly_cost: Optional[float] = None
    events: List[AlertEvent] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        """True when at least one alert condition fired."""
        return bool(self.events)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "monthly_usd": self.monthly_usd,
            "max_monthly_cost": self.max_monthly_cost,
            "events": [e.to_dict() for e in self.events],
        }


@dataclass
class WebhookTarget:
    """One webhook subscription: URL, payload format, event filter."""

    url: str
    format: str
    events: Optional[List[str]] = None

    def accepts(self, event: AlertEvent) -> bool:
        """True when this target subscribes to *event*'s type."""
        return self.events is None or event.event_type in self.events


@dataclass
class AlertConfig:
    """Parsed ``.infra-alert.yml`` file."""

    max_monthly_cost: Optional[float] = None
    webhooks: List[WebhookTarget] = field(default_factory=list)


def evaluate_alerts(
    program: n.Program,
    *,
    source: str,
    max_monthly_cost: Optional[float] = None,
    drift_report: Optional[Any] = None,
    events: Optional[Iterable[str]] = None,
) -> AlertContext:
    """Evaluate *program* against the alert conditions.

    *events* narrows which conditions are evaluated (default: all). Security
    counts SEC* findings with severity ``error`` only — warnings (e.g. a
    mutable tag hint) stay in `infra ci-comment` reports and do not page the
    team.
    """
    wanted = set(events) if events is not None else set(ALL_EVENTS)
    total = estimate_cost(program).total_monthly_usd
    found: List[AlertEvent] = []

    if (
        EVENT_COST_EXCEEDED in wanted
        and max_monthly_cost is not None
        and total > max_monthly_cost
    ):
        over = round(total - max_monthly_cost, 2)
        found.append(
            AlertEvent(
                EVENT_COST_EXCEEDED,
                "Monthly cost limit exceeded",
                [
                    f"Estimated ${total:.2f}/mo exceeds the "
                    f"${max_monthly_cost:.2f} limit by ${over:.2f}."
                ],
            )
        )

    if EVENT_SECURITY in wanted:
        sec_errors = [
            f
            for f in SecurityChecker().check(program)
            if isinstance(f, ValidationError)
        ]
        if sec_errors:
            found.append(
                AlertEvent(
                    EVENT_SECURITY,
                    f"Security violations ({len(sec_errors)})",
                    [f"{f.code}: {f.message}" for f in sec_errors],
                )
            )

    if (
        EVENT_DRIFT in wanted
        and drift_report is not None
        and drift_report.has_drift
    ):
        found.append(
            AlertEvent(
                EVENT_DRIFT,
                f"Configuration drift detected ({drift_report.target})",
                [
                    f"{i.resource}: {i.parameter} live={i.live!r} "
                    f"expected={i.expected!r}"
                    for i in drift_report.items
                ],
            )
        )

    return AlertContext(
        source=source,
        monthly_usd=total,
        max_monthly_cost=max_monthly_cost,
        events=found,
    )


def _summary_text(ctx: AlertContext) -> str:
    if not ctx.triggered:
        return f"Infra Lang: {ctx.source} OK (${ctx.monthly_usd:.2f}/mo)"
    kinds = ", ".join(e.event_type for e in ctx.events)
    return f"Infra Lang alert — {ctx.source}: {kinds}"


def _cost_fact(ctx: AlertContext) -> str:
    text = f"${ctx.monthly_usd:.2f}/mo"
    if ctx.max_monthly_cost is not None:
        text += f" (limit ${ctx.max_monthly_cost:.2f})"
    return text


def _build_slack_payload(ctx: AlertContext) -> Dict[str, Any]:
    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 Infra Lang Alert"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*File:*\n`{ctx.source}`"},
                {"type": "mrkdwn", "text": f"*Monthly cost:*\n{_cost_fact(ctx)}"},
            ],
        },
    ]
    if not ctx.triggered:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "✅ All checks green — no alert conditions triggered.",
                },
            }
        )
    for event in ctx.events:
        body = f"*{event.title}*"
        if event.lines:
            body += "\n" + "\n".join(f"• {line}" for line in event.lines)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": body}})
    return {"text": _summary_text(ctx), "blocks": blocks}


def _build_teams_payload(ctx: AlertContext) -> Dict[str, Any]:
    sections: List[Dict[str, Any]] = [
        {
            "activityTitle": "🚨 Infra Lang Alert",
            "facts": [
                {"name": "File", "value": ctx.source},
                {"name": "Monthly cost", "value": _cost_fact(ctx)},
            ],
            "markdown": True,
        }
    ]
    if not ctx.triggered:
        sections.append(
            {"title": "Status", "text": "✅ All checks green.", "markdown": True}
        )
    for event in ctx.events:
        sections.append(
            {
                "title": event.title,
                "text": "\n\n".join(event.lines) if event.lines else "—",
                "markdown": True,
            }
        )
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "D63301" if ctx.triggered else "2EB886",
        "summary": _summary_text(ctx),
        "sections": sections,
    }


def _build_discord_payload(ctx: AlertContext) -> Dict[str, Any]:
    fields: List[Dict[str, Any]] = [
        {"name": "Monthly cost", "value": _cost_fact(ctx), "inline": True}
    ]
    if not ctx.triggered:
        fields.append(
            {
                "name": "Status",
                "value": "✅ All checks green.",
                "inline": False,
            }
        )
    for event in ctx.events:
        fields.append(
            {
                "name": event.title,
                "value": "\n".join(event.lines) if event.lines else "—",
                "inline": False,
            }
        )
    return {
        "username": "Infra Lang",
        "embeds": [
            {
                "title": "🚨 Infra Lang Alert",
                "description": f"`{ctx.source}`",
                "color": _COLOR if ctx.triggered else 0x2EB886,
                "fields": fields,
            }
        ],
    }


def build_payload(format: str, ctx: AlertContext) -> Dict[str, Any]:  # noqa: A002
    """Build the JSON payload for *format* (slack | teams | discord)."""
    if format == "slack":
        return _build_slack_payload(ctx)
    if format == "teams":
        return _build_teams_payload(ctx)
    if format == "discord":
        return _build_discord_payload(ctx)
    raise ValueError(
        f"unknown webhook format {format!r}; valid: {', '.join(FORMATS)}"
    )


def mask_url(url: str) -> str:
    """Return ``scheme://host/***`` — webhook paths carry secrets."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "***"
    if not parts.scheme or not parts.netloc:
        return "***"
    return f"{parts.scheme}://{parts.netloc}/***"


def post_webhook(
    url: str, payload: Dict[str, Any], timeout: float = DEFAULT_TIMEOUT
) -> Tuple[bool, str]:
    """POST *payload* as JSON to *url*; return ``(ok, detail)``.

    Never raises: network and HTTP problems are reported as ``(False, why)``
    so a broken webhook fails the CI step cleanly instead of crashing it.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"infra-lang/{__version__}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, str(reason)


def _validate_events(value: Any, *, where: str) -> Optional[List[str]]:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(e, str) for e in value):
        raise AlertConfigError(f"{where}: 'events' must be a list of strings")
    unknown = [e for e in value if e not in ALL_EVENTS]
    if unknown:
        raise AlertConfigError(
            f"{where}: unknown event(s) {unknown}; valid: {list(ALL_EVENTS)}"
        )
    return list(value)


def load_alert_config(path: Path) -> AlertConfig:
    """Load and validate an ``.infra-alert.yml`` configuration file.

    Expected shape::

        max_monthly_cost: 500
        webhooks:
          - url: "https://hooks.slack.com/services/..."  # secret!
            format: slack
            events: [drift, cost_exceeded, security_violation]
    """
    from ruamel.yaml import YAML

    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AlertConfigError(f"{path}: cannot parse YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise AlertConfigError(f"{path}: expected a mapping at the top level")

    max_cost = data.get("max_monthly_cost")
    if max_cost is not None and not isinstance(max_cost, (int, float)):
        raise AlertConfigError(f"{path}: 'max_monthly_cost' must be a number")

    raw_hooks = data.get("webhooks", [])
    if not isinstance(raw_hooks, list):
        raise AlertConfigError(f"{path}: 'webhooks' must be a list")

    webhooks: List[WebhookTarget] = []
    for index, item in enumerate(raw_hooks):
        where = f"{path}: webhooks[{index}]"
        if not isinstance(item, dict):
            raise AlertConfigError(f"{where} must be a mapping")
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            raise AlertConfigError(f"{where}: 'url' is required")
        fmt = str(item.get("format", "slack")).lower()
        if fmt not in FORMATS:
            raise AlertConfigError(
                f"{where}: unknown format {fmt!r}; valid: {list(FORMATS)}"
            )
        webhooks.append(
            WebhookTarget(
                url=url.strip(),
                format=fmt,
                events=_validate_events(item.get("events"), where=where),
            )
        )
    return AlertConfig(
        max_monthly_cost=float(max_cost) if max_cost is not None else None,
        webhooks=webhooks,
    )
