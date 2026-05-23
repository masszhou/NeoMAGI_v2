"""storage — Postgres repositories, JSONL import/export, audit writer.

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §NeoMAGI Postgres Schema (line 530–627),
              §Structured Session Export (line 1064–1080).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - packages/coding-agent/src/core/session-manager.ts
  - packages/coding-agent/docs/session.md
"""

from .audit_repository import InMemoryAuditRepository, PostgresAuditRepository
from .audit_read_models import AuditDashboardRow, shape_audit_dashboard_row
from .audit_sink import PostgresAuditSink
from .config import (
    ConfigSource,
    DatabaseConfig,
    DatabaseConfigError,
    describe_database_config_source,
    load_database_config,
    read_env_template,
    resolve_database_config,
    would_fall_back_to,
)
from .connection import DatabaseConnectionError, connect_database
from .schema import SchemaBootstrapError, ensure_schema
from .session_jsonl import SessionJsonlError, export_session_jsonl, import_session_jsonl
from .in_memory_session_repository import InMemorySessionRepository
from .session_repository import PostgresSessionRepository
from .taskrun_repository import PostgresTaskRunRepository

__all__ = [
    "ConfigSource",
    "AuditDashboardRow",
    "DatabaseConfig",
    "DatabaseConfigError",
    "DatabaseConnectionError",
    "InMemoryAuditRepository",
    "InMemorySessionRepository",
    "PostgresAuditRepository",
    "PostgresAuditSink",
    "PostgresSessionRepository",
    "PostgresTaskRunRepository",
    "SchemaBootstrapError",
    "SessionJsonlError",
    "connect_database",
    "describe_database_config_source",
    "ensure_schema",
    "export_session_jsonl",
    "import_session_jsonl",
    "load_database_config",
    "read_env_template",
    "resolve_database_config",
    "shape_audit_dashboard_row",
    "would_fall_back_to",
]
