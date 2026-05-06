---
doc_id: 019de8b1-60b6-7780-8781-ffbbc6beb53e
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-02T14:36:59+02:00
---
# `agent_session_labels`

Mutable label projection for session entries.

## Grain

One row per `(session_id, target_pi_export_id)` label target.

## Columns

| Column | Meaning |
| --- | --- |
| `session_id` | Durable session this label belongs to. |
| `target_entry_id` | Optional DB entry id for the labeled entry. |
| `target_pi_export_id` | Pi-compatible target entry id. |
| `label` | Current label value; null means cleared. |
| `updated_at` | Last label update timestamp. |

## Relationships

- `session_id` references `agent_sessions.id`.
- `target_entry_id` references `agent_session_entries.id`.
- Primary key is `(session_id, target_pi_export_id)`.

## Notes

- Label changes are also represented as `label` entries in
  `agent_session_entries`; this table keeps the latest label state easy to query.
- `target_pi_export_id` is required so labels survive JSONL-style id projection.

## References

- `packages/neomagi_pi/src/storage/schema.py`
- `design_docs/architecture/p1_pi_cli_technical_architecture.md` § NeoMAGI Postgres Schema
