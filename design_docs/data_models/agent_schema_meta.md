---
doc_id: 019de8b1-5fa6-71cc-8c59-bf4de2264bf2
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-02T14:36:59+02:00
---
# `agent_schema_meta`

Small metadata table for schema compatibility checks.

## Grain

One row per metadata key.

## Columns

| Column | Meaning |
| --- | --- |
| `key` | Metadata key. Primary key. |
| `value` | Expected value for the key. |
| `updated_at` | Timestamp for when this metadata row was written. |

## Current Keys

| Key | Meaning |
| --- | --- |
| `neomagi_session_schema_version` | NeoMAGI durable session schema version. |
| `pi_session_version` | Pi-compatible JSONL session format version. |

## Notes

- `ensure_schema()` inserts missing keys and fails fast if an existing key has
  an unexpected value.
- This table is intentionally tiny; it is for compatibility gating, not runtime
  audit history.

## References

- `src/storage/schema.py`
- `design_docs/decisions/0007-database-hard-dependency-fail-fast.md`
- `design_docs/roadmap/p1_engine_pi.md` § P1-M6: Session Manager
