---
doc_id: 019de8b1-6001-70f1-9260-2a4d6b146075
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-02T14:36:59+02:00
---
# `agent_session_entries`

Canonical persisted session entry tree. This is the truth used to rebuild
session context and JSONL projection.

## Grain

One row per Pi-compatible session entry.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | Internal DB UUID for this entry. Primary key. |
| `session_id` | Durable session this entry belongs to. |
| `parent_entry_id` | Optional DB parent entry for tree navigation. |
| `pi_export_id` | Pi-compatible short entry id used in JSONL and slash commands. |
| `entry_type` | Entry kind, such as `message`, `label`, or `session_info`. |
| `occurred_at` | Logical occurrence timestamp from the entry payload. |
| `payload` | Full validated session entry JSON. |
| `context_participates` | Whether this entry contributes to rebuilt model context. |
| `created_at` | DB insert timestamp. |

## Relationships

- `session_id` references `agent_sessions.id`.
- `parent_entry_id` references `agent_session_entries.id`.
- `(session_id, pi_export_id)` is unique.
- `agent_messages.session_entry_id` projects message entries from this table.
- `agent_audit_events.entry_id` may point back to the relevant entry.

## Notes

- This table is canonical; projection tables should be rebuildable from
  `payload` plus tool/audit writers.
- `pi_export_id` is intentionally not the DB primary key.
- `payload` preserves opaque Pi/NeoMAGI-compatible fields for import/export.

## References

- `packages/neomagi_pi/src/storage/schema.py`
- `design_docs/architecture/p1_pi_cli_technical_architecture.md` § Pi-Compatible Entry Schema
- `design_docs/architecture/p1_pi_cli_technical_architecture.md` § NeoMAGI Postgres Schema
- `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
