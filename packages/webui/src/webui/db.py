"""Read-only Postgres access helpers for the dashboard."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from storage.config import DatabaseConfig
from storage.connection import connect_database


class ReadOnlySqlViolation(RuntimeError):
    """Raised when dashboard query code attempts to run write SQL."""


FORBIDDEN_SQL_WORDS = frozenset(
    {
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "truncate",
        "merge",
        "copy",
        "grant",
        "revoke",
        "vacuum",
    }
)


@contextmanager
def read_only_connection(
    config: DatabaseConfig,
    *,
    connection_factory=connect_database,
) -> Iterator[Any]:
    conn = connection_factory(config)
    try:
        begin_read_only(conn)
        yield conn
    finally:
        try:
            conn.rollback()
        finally:
            close = getattr(conn, "close", None)
            if callable(close):
                close()


def begin_read_only(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("BEGIN READ ONLY")


def recover_read_only(conn: Any) -> None:
    conn.rollback()
    begin_read_only(conn)


def execute_fetchall(cur: Any, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    assert_read_only_sql(sql)
    cur.execute(sql, params)
    return list(cur.fetchall())


def execute_fetchone(cur: Any, sql: str, params: tuple[Any, ...] = ()) -> Any | None:
    assert_read_only_sql(sql)
    cur.execute(sql, params)
    return cur.fetchone()


def assert_read_only_sql(sql: str) -> None:
    compact = " ".join(sql.lower().replace(";", " ").split())
    words = set(compact.replace(",", " ").replace("(", " ").replace(")", " ").split())
    forbidden = sorted(FORBIDDEN_SQL_WORDS & words)
    if forbidden:
        raise ReadOnlySqlViolation(
            "dashboard SQL must be read-only; forbidden keyword(s): "
            + ", ".join(forbidden)
        )


def quote_schema_name(schema: str) -> str:
    if not schema or not (schema[0].isalpha() or schema[0] == "_"):
        raise ValueError(f"invalid PostgreSQL schema identifier: {schema!r}")
    if not all(ch.isalnum() or ch == "_" for ch in schema):
        raise ValueError(f"invalid PostgreSQL schema identifier: {schema!r}")
    return f'"{schema}"'


__all__ = [
    "ReadOnlySqlViolation",
    "assert_read_only_sql",
    "begin_read_only",
    "execute_fetchall",
    "execute_fetchone",
    "quote_schema_name",
    "read_only_connection",
    "recover_read_only",
]
