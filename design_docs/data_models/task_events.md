---
doc_id: 019e2544-a875-7573-8919-906ae8fff89b
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-14T08:55:17+02:00
---
# `task_events`

Append-only event ledger for TaskRun lifecycle and projection.

## Grain

One row per TaskRun event.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | Task event UUID. Primary key. |
| `task_run_id` | TaskRun this event belongs to. |
| `step_id` | Optional TaskRun step this event belongs to. |
| `event_type` | Event type identifier. |
| `payload` | Event payload JSON. |
| `occurred_at` | Event occurrence timestamp. |

## Relationships

- `task_run_id` references `task_runs.id`.
- `step_id` references `task_steps.id` and may be null for task-level events.

## Indexes

- `task_events_task_run_order_idx` supports chronological event streaming by
  `(task_run_id, occurred_at, id)`.

## Notes

- `step_id` is nullable for TaskRun-level events such as start, close,
  cancellation, stale recovery, or projection rebuild.
- Workspace `events.jsonl` is a pure projection of this table's event stream;
  non-event notices belong in `state.json` or `summary.md`.
- Ordinary `taskrun status` reads DB truth without appending projection rebuild
  events. State repair, such as stale-running recovery, still writes an event.

## References

- `packages/magipi/src/storage/schema.py`
- `design_docs/architecture/p2_taskrun_architecture.md`
- `design_docs/roadmap/p2_taskrun.md`
- `design_docs/decisions/0008-memory-truth-closure-postgres-with-workspace-projection.md`
