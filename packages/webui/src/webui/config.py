"""Configuration loading for the operator WebUI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from storage.config import ConfigSource, DatabaseConfig, resolve_database_config

from .auth import AuthConfigError, validate_password_hash


class WebUIConfigError(RuntimeError):
    """Raised when WebUI configuration is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class WebUIConfig:
    password_hash: str
    session_secret: str
    cookie_secure: bool
    host: str
    port: int
    database: DatabaseConfig
    database_source: ConfigSource

    @property
    def safe_database_source_label(self) -> str:
        if self.database_source.kind == "env":
            return "environment"
        return Path(self.database_source.label).name


def load_webui_config(
    *,
    env: Mapping[str, str] | None = None,
    database_env_file: str | Path | None = None,
) -> WebUIConfig:
    values = env if env is not None else os.environ
    password_hash = (values.get("WEBUI_PASSWORD_HASH") or "").strip()
    if not password_hash:
        raise WebUIConfigError("WEBUI_PASSWORD_HASH is required")
    try:
        validate_password_hash(password_hash)
    except AuthConfigError as exc:
        raise WebUIConfigError(str(exc)) from exc

    session_secret = (values.get("WEBUI_SESSION_SECRET") or "").strip()
    if len(session_secret) < 32:
        raise WebUIConfigError("WEBUI_SESSION_SECRET must be at least 32 characters")

    database, source = resolve_database_config(env=values, env_file=database_env_file)
    return WebUIConfig(
        password_hash=password_hash,
        session_secret=session_secret,
        cookie_secure=_parse_bool(values.get("WEBUI_COOKIE_SECURE"), default=False),
        host=(values.get("WEBUI_HOST") or "127.0.0.1").strip() or "127.0.0.1",
        port=_parse_port(values.get("WEBUI_PORT") or "8787"),
        database=database,
        database_source=source,
    )


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise WebUIConfigError("WEBUI_COOKIE_SECURE must be a boolean")


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise WebUIConfigError("WEBUI_PORT must be an integer") from exc
    if port <= 0 or port > 65535:
        raise WebUIConfigError("WEBUI_PORT must be between 1 and 65535")
    return port


__all__ = ["WebUIConfig", "WebUIConfigError", "load_webui_config"]
