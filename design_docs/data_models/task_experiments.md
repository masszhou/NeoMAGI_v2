---
doc_id: 019e2544-a875-7573-8919-906ce2dc824c
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-14T08:55:17+02:00
---
# `task_experiments`

Durable experiment records attached to TaskRun steps.

## Grain

One row per experiment attempt inside a TaskRun step.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | Experiment UUID. Primary key. |
| `task_run_id` | TaskRun this experiment belongs to. |
| `step_id` | Step this experiment belongs to. |
| `hypothesis` | Hypothesis or reason for the experiment. |
| `change` | JSON description of the proposed or applied change. |
| `command` | JSON command/test invocation metadata. |
| `metrics` | JSON metrics captured during the experiment. |
| `result` | JSON result details. |
| `decision` | Outcome decision for the experiment. |
| `diff_ref` | JSON reference to relevant diff/workspace state. |
| `created_at` | Experiment creation timestamp. |

## Relationships

- `task_run_id` references `task_runs.id`.
- `step_id` references `task_steps.id`.

## Notes

- Experiment data is a durable child record of `task_steps`; it is not buried
  only in `task_steps.output`.
- The table is for audit and later comparison of experiment attempts, command
  evidence, metrics, and accepted/rejected outcomes.
- P2-M1 defines the table shape; later milestones own experiment execution and
  decision vocabulary.

## References

- `packages/magipi/src/storage/schema.py`
- `design_docs/architecture/p2_taskrun_architecture.md`
- `design_docs/roadmap/p2_taskrun.md`
