---
doc_id: 019de8b1-5f77-7665-b241-e3c4f2d39c77
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-02T14:36:59+02:00
---
# Data Models Index

This directory documents the current NeoMAGI application tables in the configured
Postgres business schema, normally `neomagi`.

Current schema source: `packages/neomagi_pi/src/storage/schema.py`.

The current M6 schema has **7** `agent_*` tables:

| Table | Purpose |
| --- | --- |
| `agent_schema_meta` | Schema and compatible protocol version metadata. |
| `agent_sessions` | Durable session root records. |
| `agent_session_entries` | Canonical Pi-compatible session entry tree. |
| `agent_messages` | Query-friendly projection of message entries. |
| `agent_tool_executions` | Tool call lifecycle and result audit projection. |
| `agent_audit_events` | Policy/audit event ledger for governed actions. |
| `agent_session_labels` | Mutable labels attached to session entries. |

Table docs:

- `design_docs/data_models/agent_schema_meta.md`
- `design_docs/data_models/agent_sessions.md`
- `design_docs/data_models/agent_session_entries.md`
- `design_docs/data_models/agent_messages.md`
- `design_docs/data_models/agent_tool_executions.md`
- `design_docs/data_models/agent_audit_events.md`
- `design_docs/data_models/agent_session_labels.md`

Current non-tables:

- `pg_search` and `vector` are installed extensions, not NeoMAGI business tables.
- Architecture mentions future optional tables such as `agent_session_exports`,
  `agent_extension_state`, and `agent_resource_snapshot`; they are not part of
  the current schema.

## References

- `packages/neomagi_pi/src/storage/schema.py`
- `design_docs/architecture/p1_pi_cli_technical_architecture.md` § Durable Session Architecture
- `design_docs/roadmap/p1_engine_pi.md` § P1-M6: Session Manager
- `design_docs/decisions/0004-use-postgresql-pgvector-instead-of-sqlite.md`
- `design_docs/decisions/0006-database-schema-default-neomagi.md`
- `design_docs/decisions/0007-database-hard-dependency-fail-fast.md`
- `design_docs/decisions/0008-memory-truth-closure-postgres-with-workspace-projection.md`
