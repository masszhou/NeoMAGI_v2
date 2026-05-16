"""Idempotent Postgres schema bootstrap for durable sessions and TaskRuns."""

from __future__ import annotations

from cli.core.session_types import CURRENT_SESSION_VERSION

from .config import DatabaseConfig

NEOMAGI_SESSION_SCHEMA_VERSION = "1"
NEOMAGI_TASKRUN_SCHEMA_VERSION = "1"

_SCHEMA_SQL_TEMPLATES = (
    """
    CREATE TABLE IF NOT EXISTS {schema}.agent_schema_meta(
        key text primary key,
        value text not null,
        updated_at timestamptz not null default now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.agent_sessions(
        id uuid primary key,
        parent_session_id uuid null references {schema}.agent_sessions(id),
        cwd text not null,
        created_at timestamptz not null,
        updated_at timestamptz not null,
        current_leaf_entry_id uuid null,
        provider_cache_affinity_id text not null,
        display_name text null,
        source jsonb not null default '{{}}'::jsonb,
        deleted_at timestamptz null
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.agent_session_entries(
        id uuid primary key,
        session_id uuid not null references {schema}.agent_sessions(id),
        parent_entry_id uuid null references {schema}.agent_session_entries(id),
        pi_export_id text not null,
        entry_type text not null,
        occurred_at timestamptz not null,
        payload jsonb not null,
        context_participates boolean not null,
        created_at timestamptz not null,
        unique(session_id, pi_export_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.agent_messages(
        id uuid primary key,
        session_entry_id uuid not null references {schema}.agent_session_entries(id),
        session_id uuid not null references {schema}.agent_sessions(id),
        role text not null,
        content jsonb not null,
        provider text null,
        api text null,
        model text null,
        response_id text null,
        stop_reason text null,
        usage jsonb null,
        is_error boolean not null default false,
        error_message text null,
        occurred_at timestamptz not null
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.agent_tool_executions(
        id uuid primary key,
        session_id uuid not null references {schema}.agent_sessions(id),
        assistant_message_id uuid null references {schema}.agent_messages(id),
        tool_call_id text not null,
        tool_name text not null,
        args jsonb not null,
        result_content jsonb null,
        result_details jsonb null,
        is_error boolean null,
        started_at timestamptz not null,
        ended_at timestamptz null,
        duration_ms integer null,
        truncation jsonb null,
        policy_decision jsonb null,
        sandbox jsonb null,
        runtime_session_id text null,
        run_id text null
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.agent_audit_events(
        id uuid primary key,
        session_id uuid not null references {schema}.agent_sessions(id),
        entry_id uuid null references {schema}.agent_session_entries(id),
        tool_execution_id uuid null references {schema}.agent_tool_executions(id),
        event_type text not null,
        actor_type text not null,
        action text not null,
        target jsonb not null default '{{}}'::jsonb,
        decision jsonb not null default '{{}}'::jsonb,
        metadata jsonb not null default '{{}}'::jsonb,
        occurred_at timestamptz not null
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.agent_session_labels(
        session_id uuid not null references {schema}.agent_sessions(id),
        target_entry_id uuid null references {schema}.agent_session_entries(id),
        target_pi_export_id text not null,
        label text null,
        updated_at timestamptz not null,
        primary key(session_id, target_pi_export_id)
    )
    """,
)

_TASKRUN_SCHEMA_SQL_TEMPLATES = (
    """
    CREATE TABLE IF NOT EXISTS {schema}.task_runs(
        id uuid primary key,
        workspace_root text not null,
        agent_session_id uuid not null references {schema}.agent_sessions(id),
        goal text not null,
        status text not null,
        permission_profile jsonb not null,
        budget jsonb not null default '{{}}'::jsonb,
        stop_conditions jsonb not null default '{{}}'::jsonb,
        current_step_id uuid null,
        summary jsonb not null default '{{}}'::jsonb,
        heartbeat_at timestamptz null,
        created_at timestamptz not null,
        updated_at timestamptz not null,
        closed_at timestamptz null
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.task_steps(
        id uuid primary key,
        task_run_id uuid not null references {schema}.task_runs(id),
        step_index integer not null,
        title text not null,
        status text not null,
        input jsonb not null default '{{}}'::jsonb,
        output jsonb not null default '{{}}'::jsonb,
        conclusion text null,
        started_at timestamptz null,
        ended_at timestamptz null,
        unique(task_run_id, step_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.task_events(
        id uuid primary key,
        task_run_id uuid not null references {schema}.task_runs(id),
        step_id uuid null references {schema}.task_steps(id),
        event_type text not null,
        payload jsonb not null,
        occurred_at timestamptz not null
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.task_permission_decisions(
        id uuid primary key,
        task_run_id uuid not null references {schema}.task_runs(id),
        step_id uuid null references {schema}.task_steps(id),
        tool_execution_id uuid null references {schema}.agent_tool_executions(id),
        policy_request jsonb not null,
        raw_decision jsonb not null,
        resolved_decision jsonb not null,
        profile_name text not null,
        occurred_at timestamptz not null
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.task_experiments(
        id uuid primary key,
        task_run_id uuid not null references {schema}.task_runs(id),
        step_id uuid not null references {schema}.task_steps(id),
        hypothesis text not null,
        change jsonb not null default '{{}}'::jsonb,
        command jsonb not null default '{{}}'::jsonb,
        metrics jsonb not null default '{{}}'::jsonb,
        result jsonb not null default '{{}}'::jsonb,
        decision text not null,
        diff_ref jsonb not null default '{{}}'::jsonb,
        created_at timestamptz not null
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS task_runs_workspace_updated_idx
    ON {schema}.task_runs(workspace_root, updated_at DESC)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS task_runs_one_running_per_workspace_idx
    ON {schema}.task_runs(workspace_root)
    WHERE status = 'running'
    """,
    """
    CREATE INDEX IF NOT EXISTS task_steps_task_run_order_idx
    ON {schema}.task_steps(task_run_id, step_index ASC)
    """,
    """
    CREATE INDEX IF NOT EXISTS task_events_task_run_order_idx
    ON {schema}.task_events(task_run_id, occurred_at ASC, id ASC)
    """,
    """
    CREATE INDEX IF NOT EXISTS task_experiments_task_run_order_idx
    ON {schema}.task_experiments(task_run_id, created_at ASC, id ASC)
    """,
    """
    CREATE INDEX IF NOT EXISTS task_experiments_step_idx
    ON {schema}.task_experiments(step_id)
    """,
)


class SchemaBootstrapError(RuntimeError):
    """Raised when schema bootstrap or version validation fails."""


def ensure_schema(conn, config: DatabaseConfig) -> None:
    """Create the current durable storage table set and verify schema metadata."""

    schema = _quote_identifier(config.schema)
    try:
        with conn.cursor() as cur:
            _create_schema_objects(cur, schema)
            _create_current_leaf_constraint(cur, schema)
            _create_taskrun_current_step_constraint(cur, schema)
            _upsert_meta(
                cur,
                schema,
                "neomagi_session_schema_version",
                NEOMAGI_SESSION_SCHEMA_VERSION,
            )
            _upsert_meta(cur, schema, "pi_session_version", str(CURRENT_SESSION_VERSION))
            _upsert_meta(
                cur,
                schema,
                "neomagi_taskrun_schema_version",
                NEOMAGI_TASKRUN_SCHEMA_VERSION,
            )
        conn.commit()
    except Exception as exc:  # pragma: no cover - integration path
        try:
            conn.rollback()
        except Exception:
            pass
        raise SchemaBootstrapError(f"failed to bootstrap Postgres schema: {exc}") from exc


def _create_schema_objects(cur, schema: str) -> None:
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for template in _SCHEMA_SQL_TEMPLATES + _TASKRUN_SCHEMA_SQL_TEMPLATES:
        cur.execute(template.format(schema=schema))


def _create_current_leaf_constraint(cur, schema: str) -> None:
    cur.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'agent_sessions_current_leaf_fk'
                  AND conrelid = '{schema}.agent_sessions'::regclass
            ) THEN
                ALTER TABLE {schema}.agent_sessions
                ADD CONSTRAINT agent_sessions_current_leaf_fk
                FOREIGN KEY (current_leaf_entry_id)
                REFERENCES {schema}.agent_session_entries(id)
                DEFERRABLE INITIALLY DEFERRED;
            END IF;
        END
        $$;
        """
    )


def _create_taskrun_current_step_constraint(cur, schema: str) -> None:
    cur.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'task_runs_current_step_fk'
                  AND conrelid = '{schema}.task_runs'::regclass
            ) THEN
                ALTER TABLE {schema}.task_runs
                ADD CONSTRAINT task_runs_current_step_fk
                FOREIGN KEY (current_step_id)
                REFERENCES {schema}.task_steps(id)
                DEFERRABLE INITIALLY DEFERRED;
            END IF;
        END
        $$;
        """
    )


def _upsert_meta(cur, schema: str, key: str, expected: str) -> None:
    cur.execute(
        f"SELECT value FROM {schema}.agent_schema_meta WHERE key = %s",
        (key,),
    )
    row = cur.fetchone()
    if row is not None:
        value = row[0]
        if value != expected:
            raise SchemaBootstrapError(
                f"schema metadata mismatch for {key}: expected {expected}, found {value}"
            )
        return
    cur.execute(
        f"""
        INSERT INTO {schema}.agent_schema_meta(key, value, updated_at)
        VALUES (%s, %s, now())
        """,
        (key, expected),
    )


def _quote_identifier(identifier: str) -> str:
    if not identifier or not (identifier[0].isalpha() or identifier[0] == "_"):
        raise SchemaBootstrapError(f"invalid PostgreSQL schema identifier: {identifier!r}")
    if not all(ch.isalnum() or ch == "_" for ch in identifier):
        raise SchemaBootstrapError(f"invalid PostgreSQL schema identifier: {identifier!r}")
    return f'"{identifier}"'


__all__ = [
    "NEOMAGI_SESSION_SCHEMA_VERSION",
    "NEOMAGI_TASKRUN_SCHEMA_VERSION",
    "SchemaBootstrapError",
    "ensure_schema",
]
