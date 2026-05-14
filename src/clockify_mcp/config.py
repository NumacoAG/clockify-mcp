"""Settings: API key + base URLs + defaults, loaded from env and an optional TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

from .errors import ConfigError

CONFIG_APP_NAME = "clockify-mcp"

DEFAULT_API_BASE = "https://api.clockify.me/api/v1"
DEFAULT_REPORTS_API_BASE = "https://reports.api.clockify.me/v1"
DEFAULT_REQUEST_TIMEOUT = 30.0
DEFAULT_CACHE_TTL = 300


@dataclass(frozen=True)
class Settings:
    """Effective configuration for the connector."""

    api_key: str
    api_base: str = DEFAULT_API_BASE
    reports_api_base: str = DEFAULT_REPORTS_API_BASE
    default_workspace_id: str | None = None
    timezone: str | None = None
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL

    @classmethod
    def load(cls, *, config_path: Path | None = None) -> Settings:
        file_cfg = _read_config_file(config_path)
        api_key = _from_env_or_file("CLOCKIFY_API_KEY", "api_key", file_cfg)
        if not api_key:
            raise ConfigError(
                "CLOCKIFY_API_KEY is not set. Either export it in your shell or write it to "
                f"{config_path or _default_config_path()} under key `api_key`."
            )
        return cls(
            api_key=api_key,
            api_base=_from_env_or_file("CLOCKIFY_API_BASE", "api_base", file_cfg)
            or DEFAULT_API_BASE,
            reports_api_base=(
                _from_env_or_file("CLOCKIFY_REPORTS_API_BASE", "reports_api_base", file_cfg)
                or DEFAULT_REPORTS_API_BASE
            ),
            default_workspace_id=_from_env_or_file(
                "CLOCKIFY_WORKSPACE_ID", "default_workspace_id", file_cfg
            ),
            timezone=_from_env_or_file("CLOCKIFY_TIMEZONE", "timezone", file_cfg),
        )


def _default_config_path() -> Path:
    return Path(user_config_dir(CONFIG_APP_NAME)) / "config.toml"


def _read_config_file(path: Path | None) -> dict[str, Any]:
    path = path or _default_config_path()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc


def _from_env_or_file(env_key: str, file_key: str, file_cfg: dict[str, Any]) -> str | None:
    val = os.environ.get(env_key)
    if val:
        return val
    raw = file_cfg.get(file_key)
    if raw is None:
        return None
    return str(raw)
