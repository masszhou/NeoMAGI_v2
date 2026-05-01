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


def load_database_config(
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> DatabaseConfig:
    """Load DATABASE_* settings from environment, falling back to local .env.

    Environment variables deliberately win over `.env`, matching the repo's
    deployment and docker-compose conventions. The real `.env` file is never
    required by tests; callers can pass an explicit `env` mapping instead.
    """

    env_values = env if env is not None else os.environ
    dotenv_values = _read_dotenv(Path(dotenv_path) if dotenv_path is not None else Path(".env"))
    values: dict[str, str | None] = {}
    for key in (*_REQUIRED_KEYS, _SCHEMA_KEY):
        values[key] = env_values.get(key) or dotenv_values.get(key)

    missing = [key for key in _REQUIRED_KEYS if not values.get(key)]
    if missing:
        joined = ", ".join(missing)
        raise DatabaseConfigError(
            f"missing database configuration: {joined}; create .env from .env_template "
            "or export DATABASE_* variables"
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


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
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
