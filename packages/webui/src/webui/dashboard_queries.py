"""Read-only dashboard query service."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

from storage.audit_read_models import shape_audit_dashboard_row
from storage.config import DatabaseConfig

from .dashboard_schema import (
    DashboardRange,
    age_seconds,
    degraded_panel,
    int_or_zero,
    iso,
    panel_payload,
    parse_dashboard_range,
    parse_timestamp,
    short_id,
    utc_now,
)
from .db import (
    execute_fetchall,
    execute_fetchone,
    quote_schema_name,
    read_only_connection,
    recover_read_only,
)


class DashboardQueryError(RuntimeError):
    """Raised when the dashboard cannot be read at the connection level."""


class DashboardQueryService:
    def __init__(
        self,
        database: DatabaseConfig,
        *,
        database_source_label: str = "unknown",
        connection_factory: Callable[[DatabaseConfig], Any] | None = None,
        now_func: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._database_source_label = database_source_label
        self._connection_factory = connection_factory
        self._now_func = now_func
        self._schema = quote_schema_name(database.schema)

    def read_dashboard(
        self,
        *,
        range_value: str = "7d",
        show_internal: bool = False,
        hide_tmp: bool = False,
    ) -> dict[str, Any]:
        now = self._now_func()
        range_info = parse_dashboard_range(range_value, now=now)
        factory = self._connection_factory
        kwargs = {"connection_factory": factory} if factory is not None else {}
        try:
            with read_only_connection(self._database, **kwargs) as conn:
                payload: dict[str, Any] = {
                    "_meta": {
                        "generated_at": now.isoformat(),
                        "source_schema": self._database.schema,
                        "database_source": self._database_source_label,
                        "range": range_info.label,
                    }
                }
                payload["health"] = self._run_panel(
                    conn,
                    "health",
                    lambda cur: self._read_health(cur, range_info, now),
                )
                payload["panel1_taskrun_progress"] = self._run_panel(
                    conn,
                    "panel1_taskrun_progress",
                    lambda cur: self._read_taskrun_progress(cur, range_info, now),
                )
                payload["panel2_session_coherence"] = self._run_panel(
                    conn,
                    "panel2_session_coherence",
                    lambda cur: self._read_session_coherence(
                        cur,
                        range_info,
                        now,
                        hide_tmp=hide_tmp,
                    ),
                )
                payload["panel3_tool_health"] = self._run_panel(
                    conn,
                    "panel3_tool_health",
                    lambda cur: self._read_tool_health(cur, range_info, now),
                )
                payload["panel4_event_stream"] = self._run_panel(
                    conn,
                    "panel4_event_stream",
                    lambda cur: self._read_event_stream(
                        cur,
                        range_info,
                        now,
                        show_internal=show_internal,
                    ),
                )
                payload["panel5_usage_trend"] = self._run_panel(
                    conn,
                    "panel5_usage_trend",
                    lambda cur: self._read_usage_trend(cur, range_info, now),
                )
                payload["panel6_audit_recent"] = self._run_panel(
                    conn,
                    "panel6_audit_recent",
                    lambda cur: self._read_audit_recent(cur, range_info, now),
                )
                return payload
        except Exception as exc:
            raise DashboardQueryError(f"database dashboard unavailable: {exc}") from exc

    def read_audit_detail(self, audit_event_id: str) -> dict[str, Any]:
        factory = self._connection_factory
        kwargs = {"connection_factory": factory} if factory is not None else {}
        try:
            with read_only_connection(self._database, **kwargs) as conn:
                with conn.cursor() as cur:
                    row = execute_fetchone(
                        cur,
                        f"""
                        SELECT a.id, a.session_id, a.event_type, a.actor_type,
                               a.action, a.target, a.decision, a.metadata,
                               a.occurred_at, a.tool_execution_id, t.args
                        FROM {self._schema}.agent_audit_events a
                        LEFT JOIN {self._schema}.agent_tool_executions t
                          ON t.id = a.tool_execution_id
                        WHERE a.id = %s
                        """,
                        (audit_event_id,),
                    )
        except Exception as exc:
            raise DashboardQueryError(f"audit detail unavailable: {exc}") from exc
        if row is None:
            raise KeyError(audit_event_id)
        return {
            "id": str(row[0]),
            "session_id": str(row[1]),
            "event_type": row[2],
            "actor_type": row[3],
            "tool": row[4],
            "target": dict(row[5] or {}),
            "decision": dict(row[6] or {}),
            "metadata": dict(row[7] or {}),
            "occurred_at": iso(row[8]),
            "tool_execution_id": str(row[9]) if row[9] is not None else None,
            "raw_tool_args": dict(row[10] or {}) if row[10] is not None else None,
            "raw_notice": "raw joined data; may contain un-redacted operational args",
        }

    def _run_panel(
        self,
        conn: Any,
        panel_name: str,
        reader: Callable[[Any], dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            with conn.cursor() as cur:
                return reader(cur)
        except Exception as exc:
            try:
                recover_read_only(conn)
            except Exception:
                pass
            return degraded_panel(f"{panel_name}: {exc}")

    def _read_health(
        self,
        cur: Any,
        range_info: DashboardRange,
        now: datetime,
    ) -> dict[str, Any]:
        schema_meta = [
            {
                "key": row[0],
                "value": row[1],
                "updated_at": iso(row[2]),
            }
            for row in execute_fetchall(
                cur,
                f"""
                SELECT key, value, updated_at
                FROM {self._schema}.agent_schema_meta
                ORDER BY key ASC
                """,
            )
        ]
        running = execute_fetchone(
            cur,
            f"SELECT count(*) FROM {self._schema}.task_runs WHERE status = 'running'",
        )
        heartbeat = execute_fetchone(
            cur,
            f"""
            SELECT max(heartbeat_at)
            FROM {self._schema}.task_runs
            WHERE status = 'running'
            """,
        )
        blocked = execute_fetchone(
            cur,
            f"""
            SELECT
              (SELECT count(*) FROM {self._schema}.task_runs WHERE status = 'blocked'),
              (SELECT count(*) FROM {self._schema}.task_steps WHERE status = 'blocked')
            """,
        )
        params, where = _range_clause("occurred_at", range_info)
        perm_blocks = execute_fetchone(
            cur,
            f"""
            SELECT count(*)
            FROM {self._schema}.task_permission_decisions
            WHERE {where}
              AND resolved_decision->>'effect' = 'block'
            """,
            params,
        )
        tool_error = execute_fetchone(
            cur,
            f"""
            SELECT count(*), count(*) FILTER (WHERE is_error IS TRUE)
            FROM {self._schema}.agent_tool_executions
            WHERE {_range_clause("started_at", range_info)[1]}
            """,
            _range_clause("started_at", range_info)[0],
        )
        total_tools = int(tool_error[0] or 0) if tool_error else 0
        error_tools = int(tool_error[1] or 0) if tool_error else 0
        data = {
            "schema_meta": schema_meta,
            "schema_drift": _schema_drift(schema_meta),
            "running_count": int(running[0] or 0) if running else 0,
            "heartbeat_age_seconds": (
                age_seconds(heartbeat[0], now=now) if heartbeat and heartbeat[0] else None
            ),
            "blocked_taskruns": int(blocked[0] or 0) if blocked else 0,
            "blocked_steps": int(blocked[1] or 0) if blocked else 0,
            "perm_blocks_7d": int(perm_blocks[0] or 0) if perm_blocks else 0,
            "tool_error_pct_7d": (
                round(error_tools * 100 / total_tools, 1) if total_tools else 0.0
            ),
        }
        return panel_payload(data, status="ok")

    def _read_taskrun_progress(
        self,
        cur: Any,
        range_info: DashboardRange,
        now: datetime,
    ) -> dict[str, Any]:
        params, where = _range_clause("updated_at", range_info)
        distribution = [
            {"status": row[0], "count": int(row[1] or 0)}
            for row in execute_fetchall(
                cur,
                f"""
                SELECT status, count(*)
                FROM {self._schema}.task_runs
                WHERE {where}
                GROUP BY status
                ORDER BY status ASC
                """,
                params,
            )
        ]
        row_params, row_where = _range_clause("r.updated_at", range_info)
        rows = execute_fetchall(
            cur,
            f"""
            SELECT r.id, r.goal, r.status, r.heartbeat_at, r.created_at,
                   r.updated_at, count(s.id), count(s.id) FILTER (WHERE s.status = 'done')
            FROM {self._schema}.task_runs r
            LEFT JOIN {self._schema}.task_steps s ON s.task_run_id = r.id
            WHERE {row_where}
            GROUP BY r.id, r.goal, r.status, r.heartbeat_at, r.created_at, r.updated_at
            ORDER BY r.updated_at DESC, r.created_at DESC
            LIMIT 10
            """,
            row_params,
        )
        items, skipped = [], 0
        for row in rows:
            item = _taskrun_item(row, now)
            if item is None:
                skipped += 1
            else:
                items.append(item)
        status = "empty" if not distribution and not items else "ok"
        return panel_payload(
            {
                "status_distribution": distribution,
                "recent_taskruns": items,
            },
            status=status,
            skipped_count=skipped,
        )

    def _read_session_coherence(
        self,
        cur: Any,
        range_info: DashboardRange,
        now: datetime,
        *,
        hide_tmp: bool,
    ) -> dict[str, Any]:
        params, where = _range_clause("s.updated_at", range_info)
        rows = execute_fetchall(
            cur,
            f"""
            SELECT s.id, s.cwd, s.parent_session_id, s.created_at, s.updated_at,
                   s.source, count(DISTINCT e.id), count(DISTINCT m.id)
            FROM {self._schema}.agent_sessions s
            LEFT JOIN {self._schema}.agent_session_entries e ON e.session_id = s.id
            LEFT JOIN {self._schema}.agent_messages m ON m.session_id = s.id
            WHERE {where}
            GROUP BY s.id, s.cwd, s.parent_session_id, s.created_at, s.updated_at, s.source
            ORDER BY s.updated_at DESC
            LIMIT 100
            """,
            params,
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        skipped = 0
        for row in rows:
            item = _session_item(row, now)
            if item is None:
                skipped += 1
                continue
            if hide_tmp and item["is_tmp_fixture"]:
                continue
            grouped[item["cwd"]].append(item)
        workspaces = [
            {
                "cwd": cwd,
                "session_count": len(items),
                "is_tmp_fixture": all(item["is_tmp_fixture"] for item in items),
                "sessions": items[:8],
            }
            for cwd, items in sorted(
                grouped.items(),
                key=lambda pair: (-len(pair[1]), pair[0]),
            )[:5]
        ]
        return panel_payload(
            {
                "workspaces": workspaces,
                "sessions_in_top_workspace": workspaces[0]["sessions"] if workspaces else [],
            },
            status="empty" if not workspaces else "ok",
            skipped_count=skipped,
        )

    def _read_tool_health(
        self,
        cur: Any,
        range_info: DashboardRange,
        now: datetime,
    ) -> dict[str, Any]:
        params, where = _range_clause("started_at", range_info)
        tool_stats = [
            {
                "tool_name": row[0],
                "n": int(row[1] or 0),
                "errors": int(row[2] or 0),
                "err_pct": round(float(row[3] or 0), 1),
                "p50_ms": int(row[4] or 0),
                "p95_ms": int(row[5] or 0),
            }
            for row in execute_fetchall(
                cur,
                f"""
                SELECT tool_name,
                       count(*) AS n,
                       count(*) FILTER (WHERE is_error IS TRUE) AS errors,
                       CASE WHEN count(*) = 0 THEN 0
                            ELSE count(*) FILTER (WHERE is_error IS TRUE) * 100.0 / count(*)
                       END AS err_pct,
                       percentile_cont(0.50) WITHIN GROUP (ORDER BY COALESCE(duration_ms, 0)),
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY COALESCE(duration_ms, 0))
                FROM {self._schema}.agent_tool_executions
                WHERE {where}
                GROUP BY tool_name
                ORDER BY err_pct DESC, n DESC
                LIMIT 6
                """,
                params,
            )
        ]
        perm_params, perm_where = _range_clause("occurred_at", range_info)
        perm_rows = execute_fetchall(
            cur,
            f"""
            SELECT policy_request, resolved_decision, profile_name, occurred_at
            FROM {self._schema}.task_permission_decisions
            WHERE {perm_where}
              AND resolved_decision->>'effect' = 'block'
            ORDER BY occurred_at DESC
            LIMIT 8
            """,
            perm_params,
        )
        permission_blocks = [_permission_block(row, now) for row in perm_rows]
        return panel_payload(
            {
                "tool_stats": tool_stats,
                "recent_perm_blocks": permission_blocks,
            },
            status="empty" if not tool_stats and not permission_blocks else "ok",
        )

    def _read_event_stream(
        self,
        cur: Any,
        range_info: DashboardRange,
        now: datetime,
        *,
        show_internal: bool,
    ) -> dict[str, Any]:
        params, where = _range_clause("updated_at", range_info)
        taskrun = execute_fetchone(
            cur,
            f"""
            SELECT id, goal, status
            FROM {self._schema}.task_runs
            WHERE {where}
            ORDER BY CASE status
                WHEN 'running' THEN 0
                WHEN 'blocked' THEN 1
                WHEN 'pending' THEN 2
                ELSE 3
            END, updated_at DESC
            LIMIT 1
            """,
            params,
        )
        if taskrun is None:
            return panel_payload(
                {"taskrun_id": None, "taskrun_summary": None, "events": []},
                status="empty",
            )
        event_params, event_where = _range_clause("occurred_at", range_info)
        query_params = (str(taskrun[0]), *event_params)
        events = [
            _event_item(row, now)
            for row in execute_fetchall(
                cur,
                f"""
                SELECT id, task_run_id, step_id, event_type, payload, occurred_at
                FROM {self._schema}.task_events
                WHERE task_run_id = %s AND {event_where}
                ORDER BY occurred_at DESC, id DESC
                LIMIT 80
                """,
                query_params,
            )
        ]
        if not show_internal:
            events = [
                item
                for item in events
                if item["event_type"]
                not in {"task_run_projection_rebuilt", "task_run_summary_updated"}
            ]
        return panel_payload(
            {
                "taskrun_id": str(taskrun[0]),
                "taskrun_summary": {
                    "goal": taskrun[1],
                    "status": taskrun[2],
                    "short_id": short_id(taskrun[0]),
                },
                "events": events,
            },
            status="empty" if not events else "ok",
        )

    def _read_usage_trend(
        self,
        cur: Any,
        range_info: DashboardRange,
        _now: datetime,
    ) -> dict[str, Any]:
        params, where = _range_clause("occurred_at", range_info)
        rows = execute_fetchall(
            cur,
            f"""
            SELECT provider, model, usage, occurred_at
            FROM {self._schema}.agent_messages
            WHERE usage IS NOT NULL AND usage <> 'null'::jsonb AND {where}
            ORDER BY occurred_at ASC
            """,
            params,
        )
        buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
        skipped = 0
        for provider, model, usage, occurred_at in rows:
            ts = parse_timestamp(occurred_at)
            if ts is None or not isinstance(usage, dict):
                skipped += 1
                continue
            day = ts.date().isoformat()
            key = (day, str(provider or "unknown"), str(model or "unknown"))
            bucket = buckets.setdefault(
                key,
                {
                    "day": day,
                    "provider": key[1],
                    "model": key[2],
                    "message_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            bucket["message_count"] += 1
            bucket["input_tokens"] += _usage_int(
                usage,
                "input_tokens",
                "inputTokens",
                "input",
            )
            bucket["output_tokens"] += _usage_int(
                usage,
                "output_tokens",
                "outputTokens",
                "output",
            )
            bucket["total_tokens"] += _usage_int(
                usage,
                "total_tokens",
                "totalTokens",
                "total",
            )
            bucket["cost_usd"] += _usage_cost(usage)
        items = list(sorted(buckets.values(), key=lambda item: (item["day"], item["provider"])))
        return panel_payload(
            {"items": items, "totals": _usage_totals(items)},
            status="empty" if not items else "ok",
            skipped_count=skipped,
        )

    def _read_audit_recent(
        self,
        cur: Any,
        range_info: DashboardRange,
        now: datetime,
    ) -> dict[str, Any]:
        params, where = _range_clause("a.occurred_at", range_info)
        rows = execute_fetchall(
            cur,
            f"""
            SELECT a.id, a.session_id, a.event_type, a.actor_type, a.action,
                   a.decision, a.metadata, a.occurred_at, a.tool_execution_id, s.cwd
            FROM {self._schema}.agent_audit_events a
            LEFT JOIN {self._schema}.agent_sessions s ON s.id = a.session_id
            WHERE {where}
            ORDER BY a.occurred_at DESC, a.id DESC
            LIMIT 30
            """,
            params,
        )
        items, skipped = [], 0
        for row in rows:
            occurred_at = iso(row[7])
            if occurred_at is None:
                skipped += 1
                continue
            shaped = shape_audit_dashboard_row(
                event_id=str(row[0]),
                session_id=str(row[1]),
                event_type=str(row[2]),
                actor_type=str(row[3]),
                action=str(row[4]),
                decision=dict(row[5] or {}),
                metadata=dict(row[6] or {}),
                occurred_at=occurred_at,
                age_seconds=age_seconds(row[7], now=now),
                tool_execution_id=str(row[8]) if row[8] is not None else None,
                cwd=row[9],
            ).to_dict()
            items.append(shaped)
        return panel_payload(
            {"items": items},
            status="empty" if not items else "ok",
            skipped_count=skipped,
        )


def _range_clause(column: str, range_info: DashboardRange) -> tuple[tuple[Any, ...], str]:
    if range_info.since is None:
        return (), "TRUE"
    return (range_info.since,), f"{column} >= %s"


def _taskrun_item(row: Any, now: datetime) -> dict[str, Any] | None:
    updated_at = parse_timestamp(row[5])
    created_at = parse_timestamp(row[4])
    if row[0] is None or row[2] is None or updated_at is None or created_at is None:
        return None
    heartbeat_age = age_seconds(row[3], now=now) if row[3] is not None else None
    return {
        "id": str(row[0]),
        "short_id": short_id(row[0]),
        "goal": row[1] or "-",
        "status": row[2],
        "heartbeat_age_seconds": heartbeat_age,
        "age_seconds": age_seconds(created_at, now=now),
        "updated_at": updated_at.isoformat(),
        "created_at": created_at.isoformat(),
        "step_done": int(row[7] or 0),
        "step_total": int(row[6] or 0),
        "is_stale": row[2] == "running" and heartbeat_age is not None and heartbeat_age > 120,
    }


def _session_item(row: Any, now: datetime) -> dict[str, Any] | None:
    updated_at = parse_timestamp(row[4])
    created_at = parse_timestamp(row[3])
    if row[0] is None or not row[1] or updated_at is None or created_at is None:
        return None
    cwd = str(row[1])
    source = dict(row[5] or {})
    return {
        "id": str(row[0]),
        "short_id": short_id(row[0]),
        "cwd": cwd,
        "parent_session_id": str(row[2]) if row[2] is not None else None,
        "parent_short_id": short_id(row[2]) if row[2] is not None else None,
        "age_seconds": age_seconds(created_at, now=now),
        "updated_at": updated_at.isoformat(),
        "entries_count": int(row[6] or 0),
        "messages_count": int(row[7] or 0),
        "taskrun_owned": bool(source.get("taskRunOwned")),
        "is_tmp_fixture": "/tmp" in cwd or "/private/" in cwd,
    }


def _permission_block(row: Any, now: datetime) -> dict[str, Any]:
    request = dict(row[0] or {})
    decision = dict(row[1] or {})
    normalized = dict(request.get("normalizedArgs") or request.get("normalized_args") or {})
    return {
        "tool": request.get("toolName") or request.get("tool_name") or "-",
        "reason": decision.get("reason") or "-",
        "subject": _subject_from_mapping(normalized or request),
        "profile_name": row[2],
        "occurred_at": iso(row[3]),
        "age_seconds": age_seconds(row[3], now=now),
    }


def _event_item(row: Any, now: datetime) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "short_id": short_id(row[0]),
        "taskrun_id": str(row[1]),
        "step_id": str(row[2]) if row[2] is not None else None,
        "event_type": row[3],
        "summary": _event_summary(dict(row[4] or {})),
        "payload": dict(row[4] or {}),
        "occurred_at": iso(row[5]),
        "age_seconds": age_seconds(row[5], now=now),
    }


def _event_summary(payload: dict[str, Any]) -> str:
    for key in ("reason", "next_action", "title", "status", "conclusion"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:140]
    if not payload:
        return "-"
    return ", ".join(sorted(payload.keys())[:4])


def _schema_drift(meta: list[dict[str, Any]]) -> bool:
    expected = {
        "neomagi_session_schema_version": "1",
        "neomagi_taskrun_schema_version": "1",
    }
    actual = {item["key"]: item["value"] for item in meta}
    return any(actual.get(key) != value for key, value in expected.items())


def _usage_int(usage: dict[str, Any], *keys: str) -> int:
    return sum(int_or_zero(usage.get(key)) for key in keys[:1] if usage.get(key) is not None) or next(
        (int_or_zero(usage.get(key)) for key in keys[1:] if usage.get(key) is not None),
        0,
    )


def _usage_totals(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "input_tokens": sum(item["input_tokens"] for item in items),
        "output_tokens": sum(item["output_tokens"] for item in items),
        "total_tokens": sum(item["total_tokens"] for item in items),
        "cost_usd": round(sum(float(item["cost_usd"]) for item in items), 6),
    }


def _usage_cost(usage: dict[str, Any]) -> float:
    value = usage.get("cost_usd")
    if value is None:
        value = usage.get("cost")
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    if isinstance(value, dict):
        for key in ("usd", "total_usd", "estimated_usd", "amount", "total"):
            nested = value.get(key)
            if isinstance(nested, (int, float)) and not isinstance(nested, bool):
                return float(nested)
    return 0.0


def _subject_from_mapping(value: dict[str, Any]) -> str:
    for key in ("commandPreview", "command", "path", "hypothesis", "toolName"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return "-"


__all__ = ["DashboardQueryError", "DashboardQueryService"]
