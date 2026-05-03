from __future__ import annotations

import pytest

from storage.config import DatabaseConfig
from storage.session_repository import PostgresSessionRepository


class _Cursor:
    def __init__(self, row):
        self._row = row
        self._last_query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, query, _params=()):
        self._last_query = query

    def fetchone(self):
        if "RETURNING id, session_id" in self._last_query:
            return self._row
        return None


class _Conn:
    def __init__(self, row):
        self.row = row
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _Cursor(self.row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_postgres_append_entry_rolls_back_when_leaf_update_fails(monkeypatch) -> None:
    payload = {
        "type": "message",
        "id": "01entry",
        "timestamp": "2026-05-03T00:00:00Z",
        "message": {"role": "user", "content": "hello", "timestamp": 1},
    }
    row = (
        "entry-db-id",
        "session-id",
        None,
        "01entry",
        "message",
        "2026-05-03T00:00:00Z",
        payload,
        True,
        "2026-05-03T00:00:00Z",
    )
    conn = _Conn(row)
    repo = PostgresSessionRepository(
        conn,
        DatabaseConfig(
            host="localhost",
            port=5432,
            user="user",
            password="pw",
            database="db",
            schema="neomagi",
        ),
    )

    def fail_leaf_update(_cur, _session_id, _entry_id):
        raise RuntimeError("injected leaf update failure")

    monkeypatch.setattr(repo, "_update_current_leaf_tx", fail_leaf_update)

    with pytest.raises(RuntimeError, match="injected leaf update failure"):
        repo.append_entry("session-id", payload)

    assert conn.commits == 0
    assert conn.rollbacks == 1
