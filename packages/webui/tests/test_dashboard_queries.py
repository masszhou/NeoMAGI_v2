from __future__ import annotations

from datetime import UTC, datetime

import pytest
from storage.config import DatabaseConfig

from webui.dashboard_queries import DashboardQueryService, _taskrun_item, _usage_cost, _usage_int
from webui.dashboard_schema import DashboardRangeError, parse_dashboard_range
from webui.db import ReadOnlySqlViolation, assert_read_only_sql


def test_range_parser_allowlist() -> None:
    now = datetime(2026, 5, 23, tzinfo=UTC)

    assert parse_dashboard_range("24h", now=now).since is not None
    assert parse_dashboard_range("all", now=now).since is None
    with pytest.raises(DashboardRangeError):
        parse_dashboard_range("7d;drop table", now=now)


def test_query_guard_rejects_write_sql() -> None:
    assert_read_only_sql("SELECT * FROM task_runs WHERE status = %s")
    with pytest.raises(ReadOnlySqlViolation):
        assert_read_only_sql("DELETE FROM task_runs")


def test_invalid_taskrun_row_is_skipped_instead_of_rendered() -> None:
    row = (
        "019e5000-0000-7000-8000-000000000001",
        "goal",
        "running",
        None,
        "not-a-date",
        "2026-05-23T00:00:00+00:00",
        0,
        0,
    )

    assert _taskrun_item(row, datetime(2026, 5, 23, tzinfo=UTC)) is None


def test_usage_cost_parser_tolerates_object_shape() -> None:
    assert _usage_cost({"cost": {"usd": 0.12}}) == 0.12
    assert _usage_cost({"cost": {"total": 0.12}}) == 0.12
    assert _usage_cost({"cost": {"prompt": 1}}) == 0.0
    assert _usage_int({"input": 10}, "input_tokens", "inputTokens", "input") == 10


def test_panel_query_failure_degrades_without_whole_dashboard_500() -> None:
    service = DashboardQueryService(
        _db_config(),
        connection_factory=lambda _config: _ExplodingConn(),
        now_func=lambda: datetime(2026, 5, 23, tzinfo=UTC),
    )

    payload = service.read_dashboard(range_value="7d")

    assert payload["health"]["_panel"]["status"] == "degraded"
    assert payload["panel1_taskrun_progress"]["_panel"]["status"] == "degraded"
    assert payload["panel6_audit_recent"]["_panel"]["status"] == "degraded"


def _db_config() -> DatabaseConfig:
    return DatabaseConfig(
        host="localhost",
        port=5432,
        user="user",
        password="pw",
        database="db",
        schema="neomagi",
    )


class _ExplodingConn:
    def __init__(self) -> None:
        self.rollbacks = 0

    def cursor(self):
        return _ExplodingCursor()

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass


class _ExplodingCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        if query == "BEGIN READ ONLY":
            return
        raise RuntimeError("missing relation")

    def fetchone(self):
        return None

    def fetchall(self):
        return []
