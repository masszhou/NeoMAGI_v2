---
doc_id: 019e2544-a875-7573-8919-90685b84fcfa
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-14T08:55:17+02:00
---
# `task_runs`

Workspace-scoped durable root record for one TaskRun.

## Grain

One row per TaskRun.

## Columns

| Column | Meaning |
| --- | --- |
| `id` | TaskRun UUID. Primary key. |
| `workspace_root` | Creation-time workspace path for this TaskRun. |
| `agent_session_id` | Durable AgentSession owned by this TaskRun. |
| `goal` | User-facing task goal captured at TaskRun creation. |
| `status` | TaskRun lifecycle status. |
| `permission_profile` | JSON permission profile snapshot for this TaskRun. |
| `budget` | JSON budget limits such as step, denial, or deadline limits. |
| `stop_conditions` | JSON stop-condition policy for the TaskRun. |
| `current_step_id` | Current active step, when one is running or selected. |
| `summary` | Machine-written TaskRun summary used for status and projection. |
| `heartbeat_at` | Last owner heartbeat while the TaskRun is running. |
| `created_at` | TaskRun creation timestamp. |
| `updated_at` | Last TaskRun update timestamp. |
| `closed_at` | Terminal close timestamp, when applicable. |

## Relationships

- `agent_session_id` references `agent_sessions.id`.
- `current_step_id` references `task_steps.id` through a deferrable FK.
- `task_steps`, `task_events`, `task_permission_decisions`, and
  `task_experiments` reference this table through `task_run_id`.

## Indexes

- `task_runs_workspace_updated_idx` supports recent TaskRun lookup by workspace.
- `task_runs_one_running_per_workspace_idx` enforces at most one `running`
  TaskRun per workspace.

## Notes

- Postgres is TaskRun truth; `.magipi/taskruns/<id>/` is projection/export only.
- Valid statuses are `pending`, `running`, `blocked`, `completed`, `failed`,
  `cancelled`, and `archived`.
- `heartbeat_at` is the stale-running recovery signal.
- TaskRun ownership is permanent: even terminal TaskRuns keep their
  `agent_session_id` hidden from ordinary session selection paths.
- `agent_sessions.source.taskRunOwned` is diagnostic metadata; this table is the
  authoritative ownership source.

## References

- `packages/magipi/src/storage/schema.py`
- `design_docs/architecture/p2_taskrun_architecture.md`
- `design_docs/roadmap/p2_taskrun.md`
- `design_docs/decisions/0007-database-hard-dependency-fail-fast.md`
- `design_docs/decisions/0008-memory-truth-closure-postgres-with-workspace-projection.md`
