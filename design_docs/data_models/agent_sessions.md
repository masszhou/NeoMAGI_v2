---
doc_id: 019de8b1-5fd3-729c-b722-5ffe8fc5b7cc
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-02T14:36:59+02:00
---
# `agent_sessions`

Durable root record for one interactive agent session.

## Grain

One row per durable session.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | Durable session UUID. Primary key. |
| `parent_session_id` | Optional parent session for fork/clone/import lineage. |
| `cwd` | Workspace directory this session belongs to. |
| `created_at` | Session creation timestamp. |
| `updated_at` | Last session update timestamp. |
| `current_leaf_entry_id` | Current active entry leaf in the session tree. |
| `provider_cache_affinity_id` | Provider-visible cache affinity id derived from or minted for this session. |
| `display_name` | Optional human-readable session name. |
| `source` | JSON metadata about import/fork/clone source. |
| `deleted_at` | Soft-delete tombstone timestamp. |

## Relationships

- `parent_session_id` references `agent_sessions.id`.
- `current_leaf_entry_id` references `agent_session_entries.id`.
- Child tables reference `agent_sessions.id` through `session_id`.

## Notes

- Postgres session id, runtime session id, provider cache affinity id, and Pi
  export id are separate identifiers and must not be mixed.
- `cwd` is used when the CLI chooses the most recent session for the current
  workspace.
- Fork/clone mint a new session and preserve lineage through `parent_session_id`.

## References

- `src/storage/schema.py`
- `design_docs/architecture/p1_pi_cli_technical_architecture.md` § Durable Session Architecture
- `design_docs/roadmap/p1_engine_pi.md` § P1-M6: Session Manager
- `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
