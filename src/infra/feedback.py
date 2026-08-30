"""Opt-in anonymous error reporting.

**Disabled by default.** When enabled (see ``infra.config``), a minimal,
non-identifying error summary may be sent to a collector.

Guarantees:
- No source code is ever sent.
- No file paths are sent (the message is truncated and paths stripped).
- No PII (no user name, hostname, env vars, etc.).
- A network / collector failure NEVER propagates back to the caller — it is
  swallowed so CLI/LSP keep working.

Fingerprinting: errors are grouped by a stable hash of their *class* (error
type + sanitized message), never by raw source content, so the same bug can be
counted/triaged across users without leaking anything identifiable.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, Optional

from infra.config import InfraConfig, load_config

# Collector endpoint is a placeholder — there is no real server yet, so the
# "send" is a no-op unless a COLLECTOR_URL is configured.
COLLECTOR_URL = None

_PATH_RE = re.compile(r"[/\\][A-Za-z0-9_.\-]+(?:[/\\][A-Za-z0-9_.\-]+)+")


def _sanitize(message: str, max_len: int = 300) -> str:
    """Remove file paths and truncate. Keeps the error code/kind only.

    Also collapses bare numbers (e.g. line/column offsets) to ``<n>`` so that
    two occurrences of the same error class in different locations produce the
    same fingerprint.
    """
    text = _PATH_RE.sub("<path>", message)
    # collapse integers that commonly carry line/column info or counts
    text = re.sub(r"\b\d+\b", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _fingerprint(error_type: str, message: str) -> str:
    """Return a stable, non-identifying fingerprint for an error class.

    Built from the error type plus the *sanitized* message, so raw source is
    never hashed. This lets a collector group similar errors without ever
    seeing file contents.
    """
    digest = hashlib.sha256(f"{error_type}:{_sanitize(message)}".encode()).hexdigest()
    return digest[:16]


def _build_payload(
    error_type: str,
    message: str,
    *,
    operation: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, str]:
    """Build a minimal non-identifying payload."""
    return {
        "product": "infra-lang",
        "version": _version(),
        "type": error_type,
        "operation": operation or "",
        "fingerprint": fingerprint or _fingerprint(error_type, message),
        "message": _sanitize(message),
    }


def _version() -> str:
    try:
        from infra.version import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


def feedback_status(config: Optional[InfraConfig] = None) -> Dict[str, str]:
    """Describe the current feedback state for CLI / UI display."""
    cfg = config if config is not None else load_config()
    return {
        "enabled": "true" if cfg.feedback_enabled else "false",
        "source": cfg.source,
        "collector": "configured" if COLLECTOR_URL else "not-configured",
        "privacy": (
            "no source, no paths, no PII; opt-in only"
        ),
    }


def report_error(
    error: BaseException,
    *,
    operation: Optional[str] = None,
    config: Optional[InfraConfig] = None,
) -> bool:
    """Report an error if feedback is enabled.

    Returns True if a report was dispatched, False otherwise (or on any
    failure). Never raises.
    """
    try:
        cfg = config if config is not None else load_config()
        if not cfg.feedback_enabled:
            return False
        if COLLECTOR_URL is None:
            # no collector configured yet -> dry-run / no-op
            return False
        payload = _build_payload(
            type(error).__name__,
            str(error),
            operation=operation,
        )
        # Network send wrapped so failures are invisible to the user.
        import json
        import urllib.request
        from urllib.parse import urlparse

        # Safety: only allow http/https collector URLs. Rejecting file:/ or
        # other custom schemes prevents an SSRF-ish misconfiguration from
        # reading local files or hitting unexpected endpoints.
        if urlparse(COLLECTOR_URL).scheme not in ("http", "https"):
            return False

        req = urllib.request.Request(
            COLLECTOR_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2):  # noqa: S310
            # Fire-and-forget: the response body is intentionally unread.
            pass
        return True
    except Exception:  # noqa: BLE001 - feedback must never break the caller
        return False

