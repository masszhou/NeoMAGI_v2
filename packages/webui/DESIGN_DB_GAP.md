# WebUI design ↔ database gap notes

The redesign (Claude Design handoff, `NeoMAGI WebUI.html`) describes four rail
surfaces — **Chat**, **Projects**, **Members**, **System**. This implementation
pass deliberately builds only the surface that the existing Postgres schema can
fully back: **Projects (TaskRun observability)**. Everything else is recorded
here and skipped, per the agreed scope.

Schema reference: the live data model is the magipi `storage` layer
(`agent_*` session tables + `task_*` TaskRun tables). The Projects surface reads
`task_runs`, `task_steps`, and `task_experiments` and reuses magipi's own P3
Parameter Golf trajectory projection (`cli.core.taskrun_experiment_summary
.p3_experiment_trajectory_summary`) so the dashboard matches the runtime exactly.

---

## ✅ Implemented — Projects / TaskRun surface (DB-backed)

| Design element | DB source |
| --- | --- |
| Run list (Active / Closed groups) | `task_runs` (all workspaces), ordered by `updated_at` |
| Run status dot + `exp` badge + relative age | `task_runs.status`; kind = has P3 experiments |
| List stat: `N att · best <bpb>` / `N steps` | `task_experiments` count + `MIN(val_bpb) FILTER (accepted)`; `task_steps` count |
| Run detail header (goal, id, status, updated) | `task_runs` |
| Trajectory git-graph (lanes, branches, verdict nodes, best ring, running pulse) | `task_experiments` → `p3_trajectory.tree` (`parent_experiment_id` lineage) |
| Per-attempt row (val_bpb, delta vs baseline, verdict, commit, records) | projected `ParameterGolfArtifact` fields |
| Attempt detail pane (hypothesis, change, verdict reasons, significance, lineage, artifact bundle) | `task_experiments` (`hypothesis`, `result`, `diff_ref`, `metrics`) |
| Deltas vs baseline | `parameter_golf_contract.BASELINE_MEAN_VAL_BPB` via `/api/meta` |
| Completed steps timeline | `task_steps` (`step_index`, `title`, `status`, `started_at`, `ended_at`, `conclusion`) |
| Empty trajectory state for non-experiment runs | runs with no P3 experiments |

### Partial / approximated within Projects

- **List "best" value** uses `MIN(val_bpb)` over experiments whose verdict is
  `accepted` (one aggregate query, no N+1). The detail pane's "best" uses the
  runtime's full eligibility rule (`current_best_parameter_golf_artifact`,
  which also checks harness validity, required files, artifact cap, records
  ref). These agree in practice but the list value is a cheap approximation.
- **`gitStatus` / `gitTracked`** are read from the persisted
  `task_runs.summary.workspace_state` (which may lag); they are **not currently
  rendered** in the final design (the four stat cards were removed during the
  design iteration) but are exposed by the API for future use.
- **Attempt friendly id** (`attempt_0001…`) is derived by the dashboard from
  chronological `(created_at, attempt_id)` order — the DB stores only the
  experiment UUID (shown as `uid`).

### Attempt fields with NO database source (rendered as `—` / omitted)

These appear in the design mock but are not present in the `task_experiments`
schema today:

- **`seed`** — rendered as `—`.
- **`trainSeconds`** ("train Ns") — row omitted when absent.
- **`codePaths`** ("code" row) — derived opportunistically from
  `task_experiments.change` if it carries a `paths`/`files`/`code_paths` key,
  otherwise the row is omitted. The schema has no explicit changed-files list.
- **`elapsed`** (running-attempt "running · 14m") — no reliable elapsed source;
  running rows show "running" / "eval pending — training" without a duration.
- **`config`** is synthesized from `change` + `command.commandPreview` (the real
  payload is minimal, e.g. `anchor=parameter-golf-mini · python train_gpt.py`),
  not the rich diff string shown in the mock (`train_gpt.py:142 SiLU→GeLU`).

---

## ⏭️ Deferred surfaces (documented, not built)

### Chat (channels, messages, threads, kanban tasks, files)
**No DB backing.** There is no concept of chat channels, multi-party messages,
threads, or a kanban task board in the schema. `agent_messages` exists but is a
single-agent session projection (`user`/`assistant`/`toolResult`), not a
channel conversation with authors/threads. Building this would require new
tables (channels, channel_messages, threads) or mock data — out of scope.

### Members (assistants/humans roster, permissions, reminders, skills, projects)
**Mostly absent.** No members/participants table, no per-member permissions,
no reminders, no skills catalog. Partially backable slices that were *not*
built this pass:
- **Profile → Memory · Database**: row-count footprint of the 7 `agent_*`
  tables + `agent_schema_meta` versions + an active `agent_sessions` row — these
  *are* directly queryable and are a natural next increment.
- **Activity feed**: `agent_tool_executions` + `agent_audit_events` could feed
  the activity list.
- Roster identity, online/offline status, runtime config, permissions,
  reminders, and skills have **no schema** and would need new tables.

### Workspace & Artifacts browsers (Projects detail tabs)
**Filesystem-based, not DB-based.** These tabs browse the projected workspace
tree and per-attempt `records/<uid>/` artifact bundles on disk
(`task_runs.workspace_root`, `diff_ref.records_ref`), not the database. They are
outside the "align with the database" scope for this pass. The tabs are present
in the UI (faithful to the design) but render a "not in this build" placeholder.
Implementing them later means a sandboxed read-only filesystem API rooted at the
TaskRun workspace, plus the syntax-highlight preview from the design's
`codeHighlight.jsx`.

### System rail icon
Disabled in the design itself ("coming soon"); left disabled.

---

## Notes for a future pass

- The richest near-term increment is the **Members → Memory · Database** panel
  and **Activity** feed, both backed by the existing `agent_*` tables.
- If `seed` / `trainSeconds` / changed-file lists become useful, the cleanest
  fix is to persist them in `task_experiments.change` / `metrics` at write time
  in the P3 loop, after which this dashboard would surface them automatically.
- The Artifacts / Workspace browsers need a read-only, path-jailed filesystem
  endpoint before they can be built safely.
