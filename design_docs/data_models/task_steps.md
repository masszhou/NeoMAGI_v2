---
doc_id: 019e2544-a875-7573-8919-90696cc94720
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-14T08:55:17+02:00
---
# `task_steps`

Ordered semantic step records inside a TaskRun.

## Grain

One row per TaskRun step.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | Task step UUID. Primary key. |
| `task_run_id` | TaskRun this step belongs to. |
| `step_index` | Stable step order within the TaskRun. |
| `title` | Human-readable step title. |
| `status` | Step lifecycle status. |
| `input` | JSON input/context captured for this step. |
| `output` | JSON output/result captured for this step. |
| `conclusion` | Optional human-readable step conclusion. |
| `started_at` | Step start timestamp, null before execution. |
| `ended_at` | Step end timestamp, null while incomplete. |

## Relationships

- `task_run_id` references `task_runs.id`.
- `task_runs.current_step_id` may reference this table.
- `task_events`, `task_permission_decisions`, and `task_experiments` may
  reference this table through `step_id`.

## Indexes

- `unique(task_run_id, step_index)` prevents duplicate step positions.
- `task_steps_task_run_order_idx` supports ordered step listing per TaskRun.

## Notes

- A step is a semantic slice inside the TaskRun-owned AgentSession; it is not a
  separate durable session.
- Valid statuses are `pending`, `running`, `done`, `failed`, `blocked`, and
  `cancelled`.
- P2-M1 can create TaskRuns with no steps; later milestones populate this table
  when step execution lands.

## References

- `packages/magipi/src/storage/schema.py`
- `design_docs/architecture/p2_taskrun_architecture.md`
- `design_docs/roadmap/p2_taskrun.md`
