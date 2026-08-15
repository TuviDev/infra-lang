"""Local user/team configuration for Infra Lang.

Currently this manages the **opt-in anonymous error reporting** ("feedback")
mode. It is intentionally small and local:

- Default is **OFF** — nothing is ever sent unless the user opts in.
- Config lives in the project directory (``.infra-config.yaml``) or in the
  user's home directory (``~/.config/infra/config.yaml``) with the project
  file taking precedence.
- A corrupted / unreadable config must never break the CLI or LSP: any read
  error falls back to safe defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

#: Env var that can force feedback on/off without editing files (e.g. CI).
ENV_ENABLE = "INFRA_FEEDBACK"
ENV_DISABLE = "INFRA_FEEDBACK_OFF"

PROJECT_CONFIG_NAME = ".infra-config.yaml"
USER_CONFIG_PATH = Path.home() / ".config" / "infra" / "config.yaml"


@dataclass(frozen=True)
class InfraConfig:
    """Resolved configuration values."""

    feedback_enabled: bool = False
    source: str = "defaults"


def _parse_feedback(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    if isinstance(value, int):
        return value != 0
    return False


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Safely load a YAML config; never raises on corrupt input."""
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - corrupted config -> safe defaults
        return {}


def _feedback_from(data: Dict[str, Any]) -> bool:
    feedback = data.get("feedback")
    if isinstance(feedback, dict):
        return _parse_feedback(feedback.get("enabled", False))
    return False


def load_config(
    project_dir: Optional[Path] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> InfraConfig:
    """Load configuration, merging project + user files and env overrides.

    Precedence (highest wins): env vars > project file > user file > defaults.
    Never raises.
    """
    if env is None:
        env = os.environ
    data: Dict[str, Any] = {}
    source = "defaults"

    # user config
    if USER_CONFIG_PATH.exists():
        data.update(_load_yaml(USER_CONFIG_PATH))
        source = "user"

    # project config takes precedence over user
    proj_path = None
    if project_dir is not None:
        proj_path = Path(project_dir) / PROJECT_CONFIG_NAME
    else:
        proj_path = Path(PROJECT_CONFIG_NAME)
    if proj_path.exists():
        data.update(_load_yaml(proj_path))
        source = "project"

    enabled = _feedback_from(data)

    # env overrides
    if env.get(ENV_DISABLE, "").strip().lower() in ("1", "true", "yes"):
        enabled = False
        source = "env"
    elif env.get(ENV_ENABLE, "").strip().lower() in ("1", "true", "yes"):
        enabled = True
        source = "env"

    return InfraConfig(feedback_enabled=enabled, source=source)


def write_config(
    path: Path,
    feedback_enabled: bool,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a config file with the feedback flag (and optional extra keys)."""
    import yaml

    data: Dict[str, Any] = {}
    if path.exists():
        data.update(_load_yaml(path))
    data["feedback"] = {"enabled": feedback_enabled}
    if extra:
        data.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
