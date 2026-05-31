---
doc_id: 019e7d58-c60b-75f3-9074-432f6258e678
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-31T00:00:00+00:00
status: completed
date: 2026-05-31
plan: dev_docs/plans/p3_m3_experiment_semantics_backfill.md
---
# P3-M3 Closeout: Experiment Semantics Backfill

## Outcome

P3-M3 is implemented as a read-model/reducer layer over existing
`task_experiments` JSONB payloads. No schema migration, artifact table, second
runtime, WebUI write path, or autonomous loop was introduced.

Canonical truth remains:

- `task_experiments.diff_ref.parent_experiment_id`
- `task_experiments.diff_ref.records_ref`
- `task_experiments.metrics.val_bpb`
- `task_experiments.metrics.artifact_size_bytes`
- `task_experiments.result.verdict`
- `task_experiments.result.harness`
- `task_experiments.result.artifact`
- `task_experiments.result.significance`

Workspace `records/<attempt_id>/` remains the artifact/log reference location.

## Implemented

- Added P3 vocabulary constants in
  `packages/magipi/src/cli/core/parameter_golf_contract.py` for verdicts,
  compatibility decisions, significance reason, artifact eligibility reason,
  lineage diagnostics, tree diagnostics, and next-action kinds.
- Added `packages/magipi/src/cli/core/taskrun_parameter_golf_trajectory.py`.
- Added deterministic attempt tree reconstruction:
  - roots;
  - children;
  - depth;
  - root-to-node path;
  - node diagnostics for missing parent, cross-run parent, self-reference, and
    cycles;
  - tree diagnostics for duplicate attempt ids and skipped non-P3 records.
- Added deterministic `p3_trajectory` summary:
  - `current_best` reuses the M2 Parameter Golf best reducer;
  - `last_attempt` is the last P3 attempt by `created_at ASC, id ASC`;
  - `next_action` is a conservative structured candidate;
  - `tree` includes roots, count, diagnostics, and nodes.
- Extended TaskRun summary rebuild so `p3_artifacts` and `p3_trajectory` are
  regenerated from `task_experiments`, not from stale persisted summary.
- Added `magipi taskrun trajectory <task-run-id-or-prefix>` textual view.
- Added `magipi taskrun attempt --parent-experiment-id <attempt-id>`.
- Producer parent validation now fails fast unless the parent is a P3 Parameter
  Golf attempt in the same TaskRun.
- Root attempts continue to write `parent_experiment_id: null` to both DB
  `diff_ref` and the records manifest mirror.

## Projection Shape

The M3 tree node shape includes:

- `attempt_id`, `task_run_id`, `step_id`;
- `parent_experiment_id`, `children`, `depth`, `path`;
- `created_at`, `hypothesis`;
- metric/artifact/verdict/significance fields from the M2 artifact projection;
- lineage refs: `records_ref`, `commit_sha`, `branch`, `parent_commit`;
- lineage diagnostics.

The trajectory summary shape is:

- `current_best`;
- `last_attempt`;
- `next_action`;
- `tree`.

M4 Renderer can consume these fields directly from TaskRun summary or from the
`trajectory` service/CLI projection.

## Verification

Commands run from `/home/devuser/devel/NeoMAGI_v2`:

```bash
uv run ruff format packages/magipi/src/cli/core/parameter_golf_contract.py packages/magipi/src/cli/core/taskrun_parameter_golf_artifacts.py packages/magipi/src/cli/core/taskrun_parameter_golf_attempt.py packages/magipi/src/cli/core/taskrun_parameter_golf_trajectory.py packages/magipi/src/cli/core/taskrun_experiment_summary.py packages/magipi/src/cli/core/taskrun_service.py packages/magipi/src/cli/core/taskrun_views.py packages/magipi/src/cli/taskrun_commands.py tests/cli/core/test_taskrun_parameter_golf_trajectory.py tests/cli/core/test_taskrun_parameter_golf_attempt.py tests/cli/test_taskrun_commands.py
uv run ruff check packages/magipi/src/cli/core/parameter_golf_contract.py packages/magipi/src/cli/core/taskrun_parameter_golf_artifacts.py packages/magipi/src/cli/core/taskrun_parameter_golf_attempt.py packages/magipi/src/cli/core/taskrun_parameter_golf_trajectory.py packages/magipi/src/cli/core/taskrun_experiment_summary.py packages/magipi/src/cli/core/taskrun_service.py packages/magipi/src/cli/core/taskrun_views.py packages/magipi/src/cli/taskrun_commands.py tests/cli/core/test_taskrun_parameter_golf_trajectory.py tests/cli/core/test_taskrun_parameter_golf_attempt.py tests/cli/test_taskrun_commands.py
uv run pytest tests/cli/core/test_taskrun_parameter_golf_trajectory.py tests/cli/core/test_taskrun_parameter_golf_artifacts.py tests/cli/core/test_taskrun_parameter_golf_attempt.py tests/cli/test_taskrun_commands.py
uv run pytest tests/cli/core/test_taskrun_service.py tests/storage/test_taskrun_repository.py tests/cli/core/test_taskrun_experiments.py
```

Results:

- Ruff check passed.
- 67 focused P3/CLI tests passed.
- 61 TaskRun service/storage/experiment regression tests passed.

## Smoke

After the initial implementation closeout, a live A6000 smoke was run and
recorded in:

`dev_docs/logs/p3_m3_experiment_semantics_backfill_smoke_findings.md`

It created child attempt `019e7d80-525f-71f1-b2f3-a99f317af894` under reused
TaskRun `019e7a5c-f877-7684-850e-5a17051c49f5`, with parent attempt
`019e7a5d-3f1f-7201-ae07-7628bee81657`.

The live smoke verified:

- `--parent-experiment-id` writes DB `diff_ref.parent_experiment_id`;
- manifest mirror preserves the same parent id;
- `magipi taskrun trajectory` renders root/child depth and parent;
- `current_best` remains the lower older attempt;
- `last_attempt` is the new child attempt.

Future live smoke should use the same workspace hygiene:

```bash
cd /tmp/neomagi_p3_m0/parameter-golf
/home/devuser/devel/NeoMAGI_v2/.venv/bin/magipi taskrun start ...
/home/devuser/devel/NeoMAGI_v2/.venv/bin/magipi taskrun attempt ... --workspace .
```

## Deferred

- M4: WebUI Execution Narrative Renderer.
- M5: autonomous multi-attempt loop, actor strategy, stopping conditions,
  critic protocol, and final statistical significance session.
- Final Welch-test/p-value success verdict remains out of M3 scope.
