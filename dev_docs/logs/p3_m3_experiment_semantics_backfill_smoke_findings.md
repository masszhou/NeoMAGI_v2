---
doc_id: 019e7d8d-207f-71b6-b63a-36348a6aca9d
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-31T00:00:00+00:00
status: completed
date: 2026-05-31
plan: dev_docs/plans/p3_m3_experiment_semantics_backfill.md
---
# P3-M3 Experiment Semantics Backfill Live Smoke Findings

## Environment

- NeoMAGI repo: `/home/devuser/devel/NeoMAGI_v2`
- Parameter Golf workspace: `/tmp/neomagi_p3_m0/parameter-golf`
- Upstream commit: `f5c079314c4877fbb0af378c0abade5a8ca33d3a`
- Host GPU: NVIDIA RTX A6000, 49140 MiB
- Database: local `neomagi-postgres` container restarted from existing volume
- Reused TaskRun id: `019e7a5c-f877-7684-850e-5a17051c49f5`
- Parent M1 attempt id: `019e7a5d-3f1f-7201-ae07-7628bee81657`

The previous `/tmp/neomagi_p3_m0/parameter-golf` workspace had been removed, so
the smoke rebuilt it from upstream and re-downloaded the fixed `sp1024` one-shard
dataset.

## Bootstrap

```bash
podman start neomagi-postgres
mkdir -p /tmp/neomagi_p3_m0
cd /tmp/neomagi_p3_m0
git clone --depth=1 https://github.com/openai/parameter-golf.git
cd parameter-golf
uv venv --python cpython-3.12.13-linux-x86_64-gnu .venvtorch27
uv pip install --python .venvtorch27/bin/python -r requirements.txt 'torch==2.7.1'
.venvtorch27/bin/python data/cached_challenge_fineweb.py --variant sp1024 --train-shards 1
```

Dataset footprint after bootstrap:

```text
data/datasets/fineweb10B_sp1024/fineweb_train_000000.bin
data/datasets/fineweb10B_sp1024/fineweb_val_000000.bin
data/tokenizers/fineweb_1024_bpe.model
data/tokenizers/fineweb_1024_bpe.vocab
du -sh data => 310M
```

## Attempt Command

The smoke ran a real child attempt under the existing M1 TaskRun, with
`--parent-experiment-id` set to the previous accepted M1 attempt.

```bash
cd /home/devuser/devel/NeoMAGI_v2
.venv/bin/magipi taskrun attempt 019e7a5c-f877-7684-850e-5a17051c49f5 \
  --anchor parameter-golf-mini \
  --workspace /tmp/neomagi_p3_m0/parameter-golf \
  --hypothesis-file /tmp/p3_m3_child_hypothesis.md \
  --command "rm -f final_model.pt final_model.int8.ptz && RUN_ID=p3_m3_child_seed48 DATA_PATH=./data/datasets/fineweb10B_sp1024/ TOKENIZER_PATH=./data/tokenizers/fineweb_1024_bpe.model VOCAB_SIZE=1024 MAX_WALLCLOCK_SECONDS=480 VAL_LOSS_EVERY=200 SEED=48 .venvtorch27/bin/torchrun --standalone --nproc_per_node=1 train_gpt.py" \
  --seed 48 \
  --timeout-seconds 900 \
  --submission-file train_gpt.py \
  --submission-file final_model.int8.ptz \
  --parent-experiment-id 019e7a5d-3f1f-7201-ae07-7628bee81657
```

`--permission full` was intentionally not used because the current project
settings require an explicit `taskrun.permissionProfiles.full` scope. The reused
TaskRun's existing non-interactive profile allowed the host command.

## Attempt Result

```text
attempt_id: 019e7d80-525f-71f1-b2f3-a99f317af894
step_id: 019e7d80-5266-73e8-aaa8-4dcd35efbadf
decision: keep
verdict_status: accepted
records_ref: records/019e7d80-525f-71f1-b2f3-a99f317af894
val_bpb: 1.59614071
artifact_size_bytes: 9157895
reasons: single_run_valid_evidence, improved_over_baseline_mean, not_final_significance_verdict
```

Records bundle:

```text
/tmp/neomagi_p3_m0/parameter-golf/records/019e7d80-525f-71f1-b2f3-a99f317af894
README.md
submission.json
manifest.json
train_log.txt
eval_result.json
submission/train_gpt.py
submission/final_model.int8.ptz
```

Manifest/eval checks:

```text
manifest_parent: 019e7a5d-3f1f-7201-ae07-7628bee81657
manifest_val_bpb: 1.59614071
manifest_artifact_size: 9157895
manifest_verdict.status: accepted
eval_status: valid
eval_verdict.status: accepted
```

## Trajectory CLI Result

```bash
.venv/bin/magipi taskrun trajectory 019e7a5c-f877-7684-850e-5a17051c49f5
```

Output:

```text
id: 019e7a5c-f877-7684-850e-5a17051c49f5
status: pending
current_best:
- attempt_id=019e7a5d val_bpb=1.59526832 records_ref=records/019e7a5d-3f1f-7201-ae07-7628bee81657
last_attempt:
- attempt_id=019e7d80 parent=019e7a5d-3f1f-7201-ae07-7628bee81657 verdict=accepted val_bpb=1.59614071 records_ref=records/019e7d80-525f-71f1-b2f3-a99f317af894
next_action: kind=propose_next base_attempt_id=019e7a5d-3f1f-7201-ae07-7628bee81657 reason=continue_experiment
tree:
- depth=0 attempt_id=019e7a5d parent= verdict=accepted val_bpb=1.59526832 records_ref=records/019e7a5d-3f1f-7201-ae07-7628bee81657
  - depth=1 attempt_id=019e7d80 parent=019e7a5d-3f1f-7201-ae07-7628bee81657 verdict=accepted val_bpb=1.59614071 records_ref=records/019e7d80-525f-71f1-b2f3-a99f317af894
```

This confirms:

- semantic parent persisted in DB and manifest mirror;
- tree reconstruction shows root/child depth and parent;
- `current_best` remains the older lower `val_bpb` M1 attempt;
- `last_attempt` is the newly created child attempt;
- `next_action.base_attempt_id` points to current best.

## Artifact CLI Result

```text
artifacts:
* attempt_id=019e7a5d created_at=2026-05-30T19:41:55.767551+00:00 val_bpb=1.59526832 artifact_size_bytes=9141310 verdict=accepted decision=keep records_ref=records/019e7a5d-3f1f-7201-ae07-7628bee81657 reason=single_run_valid_evidence
- attempt_id=019e7d80 created_at=2026-05-31T10:20:06.495477+00:00 val_bpb=1.59614071 artifact_size_bytes=9157895 verdict=accepted decision=keep records_ref=records/019e7d80-525f-71f1-b2f3-a99f317af894 reason=single_run_valid_evidence
```

## Notes

- This is still single-run evidence, not a final statistical success verdict.
- The reused TaskRun's `workspace_root` is the NeoMAGI repo root, while the
  records bundle lives under the Parameter Golf workspace. This matches the M1
  and M2 smoke caveat; `taskrun trajectory` does not need raw records access.
- `taskrun artifacts --verify-records` was not used as the M3 smoke gate because
  the old M1 records directory had been removed from `/tmp`, and records
  verification resolves `records_ref` relative to the TaskRun workspace root.
- Parameter Golf workspace side effects are expected:
  `final_model.pt` and `records/019e7d80-.../`.

## Conclusion

P3-M3 live smoke passed on A6000. A real child attempt was created with
`--parent-experiment-id`, accepted by the harness, persisted to
`task_experiments`, mirrored in `records/<attempt_id>/manifest.json`, and rendered
by `magipi taskrun trajectory` as a semantic attempt tree.
