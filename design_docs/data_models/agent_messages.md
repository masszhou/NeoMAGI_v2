---
doc_id: 019de8b1-602f-7210-bc95-511c57c16f1e
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-02T14:36:59+02:00
---
# `agent_messages`

Query-friendly projection of message entries.

## Grain

One row per persisted message entry.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | Message projection UUID. Primary key. |
| `session_entry_id` | Canonical session entry this message came from. |
| `session_id` | Durable session this message belongs to. |
| `role` | Message role, such as `user`, `assistant`, `toolResult`, or coding extension roles. |
| `content` | Message content JSON. |
| `provider` | Provider name for assistant messages when known. |
| `api` | Provider API surface when known. |
| `model` | Model id when known. |
| `response_id` | Provider response id when present. |
| `stop_reason` | Provider or runtime stop reason. |
| `usage` | Usage/cost metadata JSON. |
| `is_error` | Whether this projected message represents an error. |
| `error_message` | Error detail for error messages. |
| `occurred_at` | Message occurrence timestamp. |

## Relationships

- `session_entry_id` references `agent_session_entries.id`.
- `session_id` references `agent_sessions.id`.
- `agent_tool_executions.assistant_message_id` may reference assistant messages.

## Notes

- This table exists for inspection and queries; the full canonical entry remains
  in `agent_session_entries.payload`.
- Runtime hydration filters or converts coding extension roles before passing
  messages into `agent_core`.

## References

- `src/storage/schema.py`
- `design_docs/architecture/p1_pi_cli_technical_architecture.md` § Durable Session Architecture
- `design_docs/architecture/pi_behavior_matrix.md` § NeoMAGI strengthened behavior
