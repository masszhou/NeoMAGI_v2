"""Postgres connection bootstrap."""

from __future__ import annotations

from typing import Any

from .config import DatabaseConfig


class DatabaseConnectionError(RuntimeError):
    """Raised when the configured Postgres database cannot be reached."""


def connect_database(config: DatabaseConfig) -> Any:
    try:
        import psycopg
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency check
        raise DatabaseConnectionError(
            "psycopg is not installed; run `uv sync` after adding the Postgres dependency"
        ) from exc

    try:
        return psycopg.connect(**config.connect_kwargs())
    except Exception as exc:  # pragma: no cover - exercised by integration smoke
        raise DatabaseConnectionError(
            f"failed to connect to Postgres at {config.host}:{config.port}/{config.database}: {exc}"
        ) from exc


__all__ = ["DatabaseConnectionError", "connect_database"]
