"""Database configuration for Postgres-backed durable session storage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class DatabaseConfigError(RuntimeError):
    """Raised when database configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    schema: str = "neomagi"

    def connect_kwargs(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "dbname": self.database,
        }


_REQUIRED_KEYS = (
    "DATABASE_HOST",
    "DATABASE_PORT",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "DATABASE_NAME",
)
_SCHEMA_KEY = "DATABASE_SCHEMA"
_ENV_FILE_KEY = "NEOMAGI_ENV_FILE"


def load_database_config(
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> DatabaseConfig:
    """Load DATABASE_* settings from environment, falling back to NeoMAGI .env.

    Environment variables deliberately win over dotenv values, matching the
    repo's deployment and docker-compose conventions. Dotenv discovery is
    scoped to NeoMAGI config, not the current workspace: explicit
    ``dotenv_path`` for tests/internal callers, then ``NEOMAGI_ENV_FILE``,
    then the app/repo root ``.env``.
    """

    env_values = env if env is not None else os.environ
    dotenv_values = _read_dotenv(
        _resolve_dotenv_path(env_values, dotenv_path),
        required=dotenv_path is None and bool(env_values.get(_ENV_FILE_KEY)),
    )
    values: dict[str, str | None] = {}
    for key in (*_REQUIRED_KEYS, _SCHEMA_KEY):
        values[key] = env_values.get(key) or dotenv_values.get(key)

    missing = [key for key in _REQUIRED_KEYS if not values.get(key)]
    if missing:
        joined = ", ".join(missing)
        raise DatabaseConfigError(
            f"missing database configuration: {joined}; export DATABASE_* variables, "
            f"set {_ENV_FILE_KEY}, or create .env from .env_template in the NeoMAGI root"
        )

    try:
        port = int(str(values["DATABASE_PORT"]))
    except ValueError as exc:
        raise DatabaseConfigError("DATABASE_PORT must be an integer") from exc
    if port <= 0 or port > 65535:
        raise DatabaseConfigError("DATABASE_PORT must be between 1 and 65535")

    schema = values.get(_SCHEMA_KEY) or "neomagi"
    if not _is_identifier(schema):
        raise DatabaseConfigError(
            "DATABASE_SCHEMA must be a simple PostgreSQL identifier "
            "(letters, numbers, and underscores; cannot start with a number)"
        )

    return DatabaseConfig(
        host=str(values["DATABASE_HOST"]),
        port=port,
        user=str(values["DATABASE_USER"]),
        password=str(values["DATABASE_PASSWORD"]),
        database=str(values["DATABASE_NAME"]),
        schema=str(schema),
    )


def _resolve_dotenv_path(
    env_values: Mapping[str, str],
    explicit_path: str | Path | None,
) -> Path:
    if explicit_path is not None:
        return Path(explicit_path).expanduser()
    configured = env_values.get(_ENV_FILE_KEY)
    if configured:
        return Path(configured).expanduser()
    return _app_root_dotenv_path()


def _app_root_dotenv_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _read_dotenv(path: Path, *, required: bool = False) -> dict[str, str]:
    if not path.is_file():
        if required:
            raise DatabaseConfigError(
                f"{_ENV_FILE_KEY} points to a missing file: {path}"
            )
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _is_identifier(value: str | None) -> bool:
    if not value:
        return False
    first = value[0]
    if not (first.isalpha() or first == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in value)


__all__ = ["DatabaseConfig", "DatabaseConfigError", "load_database_config"]
