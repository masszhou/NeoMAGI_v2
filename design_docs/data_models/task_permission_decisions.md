---
doc_id: 019e2544-a875-7573-8919-906bb03a21d9
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-14T08:55:17+02:00
---
# `task_permission_decisions`

Task-scoped audit record for permission decisions.

## Grain

One row per permission decision observed while executing a TaskRun.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | Permission decision UUID. Primary key. |
| `task_run_id` | TaskRun this decision belongs to. |
| `step_id` | Optional step this decision belongs to. |
| `tool_execution_id` | Optional lower-level tool execution this decision governed. |
| `policy_request` | Original policy request JSON. |
| `raw_decision` | Raw decision returned by the policy layer. |
| `resolved_decision` | Effective decision after TaskRun permission profile resolution. |
| `profile_name` | Permission profile name used to resolve this decision. |
| `occurred_at` | Decision timestamp. |

## Relationships

- `task_run_id` references `task_runs.id`.
- `step_id` references `task_steps.id`.
- `tool_execution_id` references `agent_tool_executions.id`.

## Notes

- This table keeps TaskRun permission behavior queryable without replacing the
  lower-level `agent_tool_executions.policy_decision` projection.
- `profile_name` is denormalized so permission profile behavior can be audited
  even if profile definitions evolve later.
- P2-M1 defines the storage contract; later milestones populate it when
  TaskRun step execution and PermissionProfile resolution land.

## References

- `packages/magipi/src/storage/schema.py`
- `design_docs/architecture/p2_taskrun_architecture.md`
- `design_docs/roadmap/p2_taskrun.md`
- `design_docs/decisions/0007-database-hard-dependency-fail-fast.md`
