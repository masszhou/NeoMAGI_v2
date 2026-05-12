---
doc_id: 019de8b1-6089-722b-a73a-618825013de2
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-02T14:36:59+02:00
---
# `agent_audit_events`

Audit ledger for governed tool and policy actions.

## Grain

One row per audit event.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | Audit event UUID. Primary key. |
| `session_id` | Durable session this event belongs to. |
| `entry_id` | Optional related session entry. |
| `tool_execution_id` | Optional related tool execution. |
| `event_type` | Audit event category. |
| `actor_type` | Actor category, such as user or agent/tool runtime. |
| `action` | Action name being audited. |
| `target` | Target object metadata JSON. |
| `decision` | Policy/audit decision JSON. |
| `metadata` | Additional metadata JSON, including run/runtime ids where relevant. |
| `occurred_at` | Event timestamp. |

## Relationships

- `session_id` references `agent_sessions.id`.
- `entry_id` references `agent_session_entries.id`.
- `tool_execution_id` references `agent_tool_executions.id`.

## Notes

- This table is the durable audit trail for user bash, model tools, and policy
  decisions.
- Secrets and raw provider payloads should not be stored here.
- `event_type` describes the audit channel; `actor_type` and `action` describe
  who did what.

## References

- `packages/magipi/src/storage/schema.py`
- `design_docs/architecture/pi_behavior_matrix.md` § NeoMAGI strengthened behavior
- `design_docs/decisions/0007-database-hard-dependency-fail-fast.md`
- `design_docs/decisions/0008-memory-truth-closure-postgres-with-workspace-projection.md`
