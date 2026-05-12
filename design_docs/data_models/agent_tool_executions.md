---
doc_id: 019de8b1-605c-76ab-aca8-66cfd217f098
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-02T14:36:59+02:00
---
# `agent_tool_executions`

Lifecycle record for one tool call.

## Grain

One row per `tool_call_id` execution in a durable session.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | Tool execution UUID. Primary key. |
| `session_id` | Durable session this tool execution belongs to. |
| `assistant_message_id` | Assistant message that requested the tool, when known. |
| `tool_call_id` | Provider/runtime tool call id. |
| `tool_name` | Tool name, such as `read`, `bash`, or `edit`. |
| `args` | Tool arguments JSON. |
| `result_content` | Tool result content JSON. |
| `result_details` | Tool result details JSON. |
| `is_error` | Whether the execution ended in an error. |
| `started_at` | Start timestamp. |
| `ended_at` | End timestamp, null while incomplete. |
| `duration_ms` | Duration in milliseconds when known. |
| `truncation` | Output truncation metadata. |
| `policy_decision` | Policy decision metadata for governed tools. |
| `sandbox` | Sandbox execution metadata. |
| `runtime_session_id` | Runtime epoch id for this CLI process/session activation. |
| `run_id` | Agent/user action run id used for audit correlation. |

## Relationships

- `session_id` references `agent_sessions.id`.
- `assistant_message_id` references `agent_messages.id`.
- `agent_audit_events.tool_execution_id` may reference this row.

## Notes

- Start and end can be written separately; an incomplete row may mean an abort,
  crash, or provider/runtime failure path.
- `runtime_session_id` and `run_id` are audit metadata, not provider-visible
  session ids.
- Policy and sandbox fields are projections for fast inspection; detailed tool
  results may also exist in `agent_session_entries.payload`.

## References

- `packages/magipi/src/storage/schema.py`
- `design_docs/architecture/p1_pi_cli_technical_architecture.md` § NeoMAGI Postgres Schema
- `design_docs/architecture/pi_behavior_matrix.md` § NeoMAGI strengthened behavior
