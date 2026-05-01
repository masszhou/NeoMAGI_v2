"""Idempotent Postgres schema bootstrap for P1-M6 durable sessions."""

from __future__ import annotations

from cli.core.session_types import CURRENT_SESSION_VERSION

from .config import DatabaseConfig

NEOMAGI_SESSION_SCHEMA_VERSION = "1"


class SchemaBootstrapError(RuntimeError):
    """Raised when schema bootstrap or version validation fails."""


def ensure_schema(conn, config: DatabaseConfig) -> None:
    """Create the M6 minimum table set and verify schema metadata."""

    schema = _quote_identifier(config.schema)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {schema}.agent_schema_meta(
                    key text primary key,
                    value text not null,
                    updated_at timestamptz not null default now()
                )
                """
            )
            cur.execute(
                f"""
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
                """
            )
            cur.execute(
                f"""
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
                """
            )
            cur.execute(
                f"""
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
                """
            )
            cur.execute(
                f"""
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
                """
            )
            cur.execute(
                f"""
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
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {schema}.agent_session_labels(
                    session_id uuid not null references {schema}.agent_sessions(id),
                    target_entry_id uuid null references {schema}.agent_session_entries(id),
                    target_pi_export_id text not null,
                    label text null,
                    updated_at timestamptz not null,
                    primary key(session_id, target_pi_export_id)
                )
                """
            )
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
            _upsert_meta(cur, schema, "neomagi_session_schema_version", NEOMAGI_SESSION_SCHEMA_VERSION)
            _upsert_meta(cur, schema, "pi_session_version", str(CURRENT_SESSION_VERSION))
        conn.commit()
    except Exception as exc:  # pragma: no cover - integration path
        try:
            conn.rollback()
        except Exception:
            pass
        raise SchemaBootstrapError(f"failed to bootstrap Postgres schema: {exc}") from exc


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


__all__ = ["NEOMAGI_SESSION_SCHEMA_VERSION", "SchemaBootstrapError", "ensure_schema"]
