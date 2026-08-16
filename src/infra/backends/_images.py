"""Shared container-image maps for the backends.

These mappings are identical across the Kubernetes and Docker Compose backends
(a given engine type maps to the same image regardless of target). Keeping them
in one place means adding a new engine type updates both backends together
instead of letting them drift. ``_DB_IMAGES`` intentionally stays backend-local
because the Compose variant carries per-type environment-variable sets that the
Kubernetes variant does not.
"""

from __future__ import annotations

from typing import Dict

CACHE_IMAGES: Dict[str, str] = {
    "redis": "redis",
    "valkey": "valkey",
    "memcached": "memcached",
}

QUEUE_IMAGES: Dict[str, str] = {
    "rabbitmq": "rabbitmq:3-management",
    "kafka": "bitnami/kafka",
    "nats": "nats",
}
