---
status: completed
date: 2026-05-30
plan: dev_docs/plans/p3_m2_artifact_first_class_object.md
---
# P3-M2 Closeout: Artifact as First-Class Object

## Outcome

P3-M2 is implemented as a metadata/read-model layer over existing
`task_experiments` JSONB payloads. No artifact table or schema migration was
added.

Canonical truth remains:

- `task_experiments.metrics.val_bpb`
- `task_experiments.metrics.artifact_size_bytes`
- `task_experiments.result.verdict`
- `task_experiments.result.harness`
- `task_experiments.result.artifact`
- `task_experiments.result.significance`
- `task_experiments.diff_ref.records_ref`

Artifact bytes and logs remain in workspace `records/<attempt_id>/`.

## Implemented

- Added shared Parameter Golf contract constants in
  `packages/magipi/src/cli/core/parameter_golf_contract.py`.
- Added P3 artifact projection helpers in
  `packages/magipi/src/cli/core/taskrun_parameter_golf_artifacts.py`.
- Added deterministic current-best selection:
  - only accepted, valid, budget-comparable, required-files-ok artifacts;
  - finite `val_bpb`;
  - artifact size under cap;
  - records/content ref present;
  - lower `val_bpb` wins;
  - tie-break by `created_at`, then `attempt_id`.
- Added `p3_artifacts` summary data without removing the existing P2
  `current_best` shape.
- Made `current_best_experiment()` P3-aware when eligible Parameter Golf
  artifacts are present.
- Added `magipi taskrun artifacts <task-run-id-or-prefix>` and
  `--verify-records`.
- `TaskRunArtifactsResult` now carries `current_best_attempt_id` computed by the
  P3 reducer, so CLI rendering does not depend on stale persisted summary.
- Artifact record detection is scoped to P3 payload signals
  (`result.verdict` / `result.artifact`) rather than generic metric names.
- Added records drift audit checks for manifest/eval JSON. This check compares
  small structured files only and does not recompute metrics or read raw logs.

## Verification

Commands run from `/home/devuser/devel/NeoMAGI_v2`:

```bash
.venv/bin/ruff format packages/magipi/src/cli/core/parameter_golf_contract.py packages/magipi/src/cli/core/taskrun_parameter_golf_artifacts.py packages/magipi/src/cli/core/taskrun_experiment_summary.py packages/magipi/src/cli/core/taskrun_parameter_golf_attempt.py packages/magipi/src/cli/core/taskrun_service.py packages/magipi/src/cli/core/taskrun_views.py packages/magipi/src/cli/taskrun_commands.py tests/cli/core/test_taskrun_parameter_golf_artifacts.py
.venv/bin/ruff check packages/magipi/src/cli/core/parameter_golf_contract.py packages/magipi/src/cli/core/taskrun_parameter_golf_artifacts.py packages/magipi/src/cli/core/taskrun_experiment_summary.py packages/magipi/src/cli/core/taskrun_parameter_golf_attempt.py packages/magipi/src/cli/core/taskrun_service.py packages/magipi/src/cli/core/taskrun_views.py packages/magipi/src/cli/taskrun_commands.py tests/cli/core/test_taskrun_parameter_golf_artifacts.py
.venv/bin/pytest -q tests/cli/core/test_taskrun_parameter_golf_artifacts.py tests/cli/core/test_taskrun_parameter_golf_attempt.py tests/cli/core/test_taskrun_experiments.py tests/cli/test_taskrun_commands.py
```

Result:

- Ruff check passed.
- 69 tests passed.

## Smoke

M2 implementation was verified with unit-level M1-compatible payload fixtures and
synthetic guard-rail records. No new live Parameter Golf smoke run was started,
so the M1 workspace hygiene note still applies for future manual smoke:

```bash
cd /tmp/neomagi_p3_m0/parameter-golf
/home/devuser/devel/NeoMAGI_v2/.venv/bin/magipi taskrun start ...
/home/devuser/devel/NeoMAGI_v2/.venv/bin/magipi taskrun attempt ... --workspace .
```

## Deferred

- M3: attempt parent tree and multi-attempt trajectory reducer.
- M4: WebUI Execution Narrative Renderer over this read-model helper.
- M5: final statistical significance session and Welch-test verdict.
- No object store, artifact table, or binary artifact persistence outside
  workspace records was introduced in M2.
