"""storage — Postgres repositories, JSONL import/export, audit writer.

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §NeoMAGI Postgres Schema (line 530–627),
              §Structured Session Export (line 1064–1080).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - packages/coding-agent/src/core/session-manager.ts
  - packages/coding-agent/docs/session.md
"""

from .audit_repository import InMemoryAuditRepository, PostgresAuditRepository
from .audit_sink import PostgresAuditSink
from .config import DatabaseConfig, DatabaseConfigError, load_database_config
from .connection import DatabaseConnectionError, connect_database
from .schema import SchemaBootstrapError, ensure_schema
from .session_jsonl import SessionJsonlError, export_session_jsonl, import_session_jsonl
from .in_memory_session_repository import InMemorySessionRepository
from .session_repository import PostgresSessionRepository

__all__ = [
    "DatabaseConfig",
    "DatabaseConfigError",
    "DatabaseConnectionError",
    "InMemoryAuditRepository",
    "InMemorySessionRepository",
    "PostgresAuditRepository",
    "PostgresAuditSink",
    "PostgresSessionRepository",
    "SchemaBootstrapError",
    "SessionJsonlError",
    "connect_database",
    "ensure_schema",
    "export_session_jsonl",
    "import_session_jsonl",
    "load_database_config",
]
