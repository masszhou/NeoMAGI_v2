"""Read-only TaskRun read-model queries for the Projects surface.

This service maps the NeoMAGI Postgres TaskRun tables (``task_runs``,
``task_steps``, ``task_experiments``) onto the JSON shape the WebUI's Projects
surface consumes. It deliberately reuses magipi's own P3 Parameter Golf
trajectory projection (``p3_experiment_trajectory_summary`` and the underlying
attempt-tree builder) so the dashboard's trajectory matches exactly what the
agent runtime computes — single source of truth, recomputed live from the
experiment ledger rather than from a possibly-stale persisted ``summary``.

All access is read-only (``BEGIN READ ONLY`` + ``SELECT`` only).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cli.core.parameter_golf_contract import (
    BASELINE_MEAN_VAL_BPB,
    BASELINE_N,
    BASELINE_SAMPLE_STD_VAL_BPB,
)
from cli.core.taskrun_experiment_summary import p3_experiment_trajectory_summary
from storage.config import DatabaseConfig
from storage.taskrun_records import TaskExperimentRecord, TaskRunRecord, TaskStepRecord
from storage.taskrun_repository import PostgresTaskRunRepository

from .db import execute_fetchall, quote_schema_name, read_only_connection


class TaskRunQueryError(RuntimeError):
    """Raised when the TaskRun read model cannot be served."""


# val_bpb baseline — frozen reference for the P3 Mini Parameter Golf anchor.
# Surfaced so the UI can render attempt deltas against the same baseline the
# runtime uses (see cli.core.parameter_golf_contract).
BASELINE: dict[str, Any] = {
    "metric": "val_bpb",
    "direction": "minimize",
    "n": BASELINE_N,
    "mean": BASELINE_MEAN_VAL_BPB,
    "std": BASELINE_SAMPLE_STD_VAL_BPB,
}


class TaskRunQueryService:
    """Read-only projection of TaskRun data for the operator WebUI."""

    def __init__(
        self,
        database: DatabaseConfig,
        *,
        database_source_label: str = "",
    ) -> None:
        self._database = database
        self._schema = quote_schema_name(database.schema)
        self._source_label = database_source_label

    # ── Meta ────────────────────────────────────────────────────────────
    def read_meta(self) -> dict[str, Any]:
        return {
            "baseline": dict(BASELINE),
            "database_schema": self._database.schema,
            "database_source": self._source_label,
        }

    # ── List (all workspaces) ───────────────────────────────────────────
    def list_runs(self) -> list[dict[str, Any]]:
        with read_only_connection(self._database) as conn:
            with conn.cursor() as cur:
                run_rows = execute_fetchall(
                    cur,
                    f"""
                    SELECT id, workspace_root, agent_session_id, goal, status,
                           created_at, updated_at, closed_at
                    FROM {self._schema}.task_runs
                    ORDER BY updated_at DESC, created_at DESC
                    """,
                )
                step_rows = execute_fetchall(
                    cur,
                    f"""
                    SELECT task_run_id, COUNT(*)
                    FROM {self._schema}.task_steps
                    GROUP BY task_run_id
                    """,
                )
                # P3 attempts (records carrying a verdict/artifact payload) and
                # the best accepted val_bpb, per run.
                exp_rows = execute_fetchall(
                    cur,
                    f"""
                    SELECT task_run_id,
                           COUNT(*) FILTER (
                               WHERE result ? 'verdict' OR result ? 'artifact'
                           ) AS p3_count,
                           MIN((metrics->>'val_bpb')::double precision) FILTER (
                               WHERE result->'verdict'->>'status' = 'accepted'
                           ) AS best_bpb
                    FROM {self._schema}.task_experiments
                    GROUP BY task_run_id
                    """,
                )
        step_counts = {str(row[0]): int(row[1]) for row in step_rows}
        exp_map = {
            str(row[0]): (int(row[1] or 0), row[2]) for row in exp_rows
        }
        items: list[dict[str, Any]] = []
        for row in run_rows:
            run_id = str(row[0])
            p3_count, best_bpb = exp_map.get(run_id, (0, None))
            items.append(
                {
                    "id": run_id,
                    "goal": row[3],
                    "status": row[4],
                    "kind": "experiment" if p3_count > 0 else "task",
                    "workspaceRoot": row[1],
                    "sessionId": str(row[2]) if row[2] is not None else None,
                    "createdAt": _iso(row[5]),
                    "updatedAt": _iso(row[6]),
                    "updated": _fmt_minute(row[6]),
                    "closedAt": _iso(row[7]),
                    "attemptCount": p3_count,
                    "bestBpb": best_bpb,
                    "stepCount": step_counts.get(run_id, 0),
                }
            )
        return items

    # ── Detail ──────────────────────────────────────────────────────────
    def get_run(self, task_run_id: str) -> dict[str, Any] | None:
        with read_only_connection(self._database) as conn:
            repo = PostgresTaskRunRepository(conn, self._database)
            record = repo.get_task_run(task_run_id)
            if record is None:
                return None
            steps = repo.list_steps(task_run_id)
            experiments = repo.list_experiments(task_run_id)
            trajectory = p3_experiment_trajectory_summary(
                experiments, task_run_id=task_run_id
            )
        return self._build_run_detail(record, steps, experiments, trajectory)

    # ── Mapping ─────────────────────────────────────────────────────────
    def _build_run_detail(
        self,
        record: TaskRunRecord,
        steps: list[TaskStepRecord],
        experiments: list[TaskExperimentRecord],
        trajectory: dict[str, Any] | None,
    ) -> dict[str, Any]:
        is_exp = trajectory is not None
        summary = record.summary or {}
        workspace_state = summary.get("workspace_state")
        git_changes = (
            workspace_state.get("status")
            if isinstance(workspace_state, dict)
            and isinstance(workspace_state.get("status"), list)
            else []
        )
        permission = None
        if isinstance(record.permission_profile, dict):
            permission = record.permission_profile.get("name")
        return {
            "id": record.id,
            "goal": record.goal,
            "status": record.status,
            "kind": "experiment" if is_exp else "task",
            "createdAt": _iso(record.created_at),
            "updatedAt": _iso(record.updated_at),
            "updated": _fmt_minute(record.updated_at),
            "closedAt": _iso(record.closed_at),
            "workspaceRoot": record.workspace_root,
            "projectionPath": f".magipi/taskruns/{record.id}",
            "sessionId": record.agent_session_id,
            "permission": permission,
            "gitStatus": (
                "dirty"
                if git_changes
                else ("clean" if isinstance(workspace_state, dict) else None)
            ),
            "gitTracked": len(git_changes),
            "nextAction": _next_action(trajectory, summary),
            "steps": [_map_step(step) for step in steps],
            "attempts": (
                _build_attempts(trajectory, experiments) if is_exp else None
            ),
        }


def _build_attempts(
    trajectory: dict[str, Any],
    experiments: list[TaskExperimentRecord],
) -> list[dict[str, Any]]:
    tree = trajectory.get("tree") or {}
    nodes = tree.get("nodes") or []
    # Present attempts chronologically (time flows down the git-graph), matching
    # the runtime's own (created_at, attempt_id) ordering.
    nodes_sorted = sorted(
        nodes,
        key=lambda n: (str(n.get("created_at") or ""), str(n.get("attempt_id") or "")),
    )
    uid_to_ordinal = {
        node["attempt_id"]: f"attempt_{index:04d}"
        for index, node in enumerate(nodes_sorted, start=1)
        if node.get("attempt_id")
    }
    best_uid = (trajectory.get("current_best") or {}).get("attempt_id")
    experiment_by_id = {experiment.id: experiment for experiment in experiments}

    attempts: list[dict[str, Any]] = []
    for node in nodes_sorted:
        uid = node.get("attempt_id")
        verdict_obj = node.get("verdict") or {}
        verdict = verdict_obj.get("status") or "running"
        metric = node.get("metric") or {}
        artifact = node.get("artifact") or {}
        lineage = node.get("lineage") or {}
        significance = node.get("significance") or {}
        experiment = experiment_by_id.get(uid)
        if verdict == "error":
            status = "failed"
        elif verdict in ("accepted", "rejected"):
            status = "done"
        else:
            status = "running"
        attempts.append(
            {
                "id": uid_to_ordinal.get(uid),
                "uid": uid,
                "parent": uid_to_ordinal.get(node.get("parent_experiment_id")),
                "val_bpb": metric.get("value"),
                "verdict": verdict,
                "status": status,
                "best": uid == best_uid,
                "hypothesis": node.get("hypothesis") or "",
                "config": _config_str(experiment),
                "codePaths": _code_paths(experiment),
                "commit": lineage.get("commit_sha"),
                "parentCommit": lineage.get("parent_commit"),
                "branch": lineage.get("branch"),
                "records": lineage.get("records_ref"),
                "artifactBytes": artifact.get("size_bytes"),
                "significance": significance.get("reason"),
                "reasons": list(verdict_obj.get("reasons") or []),
                "created": _fmt_minute(node.get("created_at")),
                # Not present in the current task_experiments schema — see
                # DESIGN_DB_GAP.md. Surfaced as null so the UI renders a dash.
                "seed": None,
                "trainSeconds": None,
            }
        )
    return attempts


def _map_step(step: TaskStepRecord) -> dict[str, Any]:
    return {
        "idx": step.step_index,
        "title": step.title,
        "status": step.status,
        "started": _fmt_second(step.started_at),
        "ended": _fmt_second(step.ended_at),
        "conclusion": step.conclusion or "",
    }


def _next_action(
    trajectory: dict[str, Any] | None,
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    if not trajectory:
        return None
    next_action = trajectory.get("next_action")
    if not isinstance(next_action, dict):
        return None
    rationale = summary.get("next_action")
    return {
        "kind": next_action.get("kind"),
        "reason": next_action.get("reason"),
        "baseUid": next_action.get("base_attempt_id"),
        "rationale": rationale if isinstance(rationale, str) else None,
    }


def _config_str(experiment: TaskExperimentRecord | None) -> str | None:
    if experiment is None:
        return None
    parts: list[str] = []
    change = experiment.change or {}
    for key, value in change.items():
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={value}")
    command_preview = (experiment.command or {}).get("commandPreview")
    if isinstance(command_preview, str) and command_preview:
        parts.append(command_preview)
    return " · ".join(parts) if parts else None


def _code_paths(experiment: TaskExperimentRecord | None) -> list[str]:
    # The task_experiments schema does not carry an explicit changed-files list;
    # derive opportunistically from the `change` payload when present.
    if experiment is None:
        return []
    change = experiment.change or {}
    for key in ("code_paths", "codePaths", "paths", "files", "path", "file"):
        value = change.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
        if isinstance(value, str) and value:
            return [value]
    return []


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_minute(value: Any) -> str | None:
    parsed = _parse(value)
    if parsed is None:
        return str(value) if value else None
    return parsed.strftime("%Y-%m-%d %H:%M")


def _fmt_second(value: Any) -> str | None:
    parsed = _parse(value)
    if parsed is None:
        return str(value) if value else None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M:%SZ")


__all__ = ["BASELINE", "TaskRunQueryError", "TaskRunQueryService"]
