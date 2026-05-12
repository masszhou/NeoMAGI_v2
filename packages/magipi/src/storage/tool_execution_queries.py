"""Postgres reads for durable tool-execution records."""

from __future__ import annotations

from typing import Any

from .session_utils import iso as _iso
from .tool_execution_records import ToolExecutionRecord


def list_tool_executions(conn, schema: str, session_id: str) -> list[ToolExecutionRecord]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, session_id, tool_call_id, tool_name, args,
                   result_content, result_details, is_error, started_at,
                   ended_at, duration_ms, truncation, policy_decision,
                   sandbox, runtime_session_id, run_id
            FROM {schema}.agent_tool_executions
            WHERE session_id = %s
            ORDER BY started_at ASC, id ASC
            """,
            (session_id,),
        )
        return [_tool_execution_from_row(row) for row in cur.fetchall()]


def _tool_execution_from_row(row: Any) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        id=str(row[0]),
        session_id=str(row[1]),
        tool_call_id=row[2],
        tool_name=row[3],
        args=row[4],
        result_content=row[5],
        result_details=row[6],
        is_error=row[7],
        started_at=_iso(row[8]),
        ended_at=_iso(row[9]) if row[9] is not None else None,
        duration_ms=row[10],
        truncation=row[11],
        policy_decision=row[12],
        sandbox=row[13],
        runtime_session_id=row[14],
        run_id=row[15],
    )


__all__ = ["list_tool_executions"]
