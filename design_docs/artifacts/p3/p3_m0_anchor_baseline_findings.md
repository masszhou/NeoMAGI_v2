---
doc_id: 019e79aa-5e3e-740a-aa3b-a95818bdd46c
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-30T16:14:22+00:00
---
# P3-M0 Anchor Baseline Findings

- Status: done
- Date: 2026-05-30
- Context: P3-M0 Anchor Setup & Narrative Vocabulary
- Anchor workspace: `/tmp/neomagi_p3_m0/parameter-golf`
- Upstream: `openai/parameter-golf`
- Upstream commit: `f5c079314c4877fbb0af378c0abade5a8ca33d3a`

## Summary

P3-M0 established the Tier 2 A6000 naive baseline for Mini Parameter Golf under the fixed mini budget.

```text
n = 5
seed set = 42, 43, 44, 45, 46
mean final val_bpb = 1.5997882960
sample std = 0.0023292502
population std = 0.0020833447
metric source = final_int8_zlib_roundtrip_exact val_bpb
```

This baseline is comparable only to future attempts run under the same upstream commit, same data boundary, same A6000 tier, same wallclock budget, same validation path, and same artifact metric.

## Fixed Budget

```text
hardware: local NVIDIA RTX A6000, 48GB
train command: torchrun --standalone --nproc_per_node=1 train_gpt.py
MAX_WALLCLOCK_SECONDS=480
VAL_LOSS_EVERY=200
VOCAB_SIZE=1024
DATA_PATH=./data/datasets/fineweb10B_sp1024/
TOKENIZER_PATH=./data/tokenizers/fineweb_1024_bpe.model
train shards downloaded: 1
validation shards downloaded: 1, full fixed first-50k-doc FineWeb validation set
artifact cap: 16,000,000 decimal bytes
```

Dataset check:

```text
fineweb_train_000000.bin 200001024
fineweb_val_000000.bin 124044716
manifest stats: files_train=195 available upstream, files_val=1, tokens_val=62021846
train log observed: train_loader:dataset:fineweb10B_sp1024 train_shards:1
train log observed: val_loader:... tokens:62021632
```

Validation was not sampled. The run used the full cached `fineweb_val_*.bin` split.

## Environment

```text
machine: deva6000
OS: Linux 6.8.0-117-generic x86_64, glibc 2.39
GPU: NVIDIA RTX A6000
nvidia-smi: NVIDIA-SMI 610.43.02, CUDA UMD Version 13.3
Python: 3.12.13 from uv standalone CPython
PyTorch: 2.7.1+cu126
torch CUDA: 12.6
```

Environment notes:

- Python 3.14.5 + torch 2.12.0+cu130 failed before baseline with `CompiledFunctionBackward returned an invalid gradient`.
- System Python 3.12.3 + torch 2.12.0+cu130 first failed because Triton could not compile without `Python.h`, then uv standalone Python 3.12.13 confirmed the remaining blocker was still torch 2.12 compiled backward.
- The accepted baseline environment pins torch to `2.7.1+cu126`; no upstream training code was changed.

Failed environment attempts are excluded from the baseline sample:

```text
logs/p3_m0_a6000_baseline/a6000_naive_seed42.log
logs/p3_m0_a6000_baseline/a6000_naive_seed42_py312.log
logs/p3_m0_a6000_baseline/a6000_naive_seed42_uv312.log
```

## Runs

| Seed | stop step | train ms | final val_loss | final val_bpb | int8+zlib artifact bytes | Log |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 42 | 367 | 480405 | 2.69479085 | 1.59600693 | 9143754 | `/tmp/neomagi_p3_m0/parameter-golf/logs/p3_m0_a6000_baseline/a6000_naive_seed42_torch27.log` |
| 43 | 366 | 480083 | 2.70187337 | 1.60020160 | 9132803 | `/tmp/neomagi_p3_m0/parameter-golf/logs/p3_m0_a6000_baseline/a6000_naive_seed43_torch27.log` |
| 44 | 366 | 480242 | 2.70165257 | 1.60007083 | 9126358 | `/tmp/neomagi_p3_m0/parameter-golf/logs/p3_m0_a6000_baseline/a6000_naive_seed44_torch27.log` |
| 45 | 366 | 480542 | 2.70562694 | 1.60242468 | 9129351 | `/tmp/neomagi_p3_m0/parameter-golf/logs/p3_m0_a6000_baseline/a6000_naive_seed45_torch27.log` |
| 46 | 366 | 480686 | 2.70193388 | 1.60023744 | 9134443 | `/tmp/neomagi_p3_m0/parameter-golf/logs/p3_m0_a6000_baseline/a6000_naive_seed46_torch27.log` |

Log hashes:

```text
322f1e3c4c54f67222ff1872df5d49666bf6322c9962e18c69ce586b2c7ab143  a6000_naive_seed42_torch27.log
8c23eab45111fa9955d26bd3687267e030267f92ddcae65746778f669ee330fa  a6000_naive_seed43_torch27.log
75ef9629d42fcf4df942c7eb2e4854c8711273f8a148ca0bcf617a86d83bf5e2  a6000_naive_seed44_torch27.log
f44a3a9167a72f2d77c317591b880690bd516b74e3fdc1e25b7fb7d83a68c4ac  a6000_naive_seed45_torch27.log
1fefccabd59391987c1ad038551797ada9d5b73d6c9b65b21d692be0b0f41297  a6000_naive_seed46_torch27.log
```

## Commands

Workspace bootstrap:

```bash
mkdir -p /tmp/neomagi_p3_m0
cd /tmp/neomagi_p3_m0
git clone --depth=1 https://github.com/openai/parameter-golf.git
cd parameter-golf
git rev-parse HEAD
uv venv --python cpython-3.12.13-linux-x86_64-gnu .venvtorch27
uv pip install --python .venvtorch27/bin/python -r requirements.txt 'torch==2.7.1'
.venvtorch27/bin/python data/cached_challenge_fineweb.py --variant sp1024 --train-shards 1
```

Baseline loop:

```bash
for SEED in 42 43 44 45 46; do
  RUN_ID="a6000_naive_seed${SEED}_torch27" \
  DATA_PATH=./data/datasets/fineweb10B_sp1024/ \
  TOKENIZER_PATH=./data/tokenizers/fineweb_1024_bpe.model \
  VOCAB_SIZE=1024 \
  MAX_WALLCLOCK_SECONDS=480 \
  VAL_LOSS_EVERY=200 \
  SEED="${SEED}" \
  timeout 900 .venvtorch27/bin/torchrun --standalone --nproc_per_node=1 train_gpt.py \
    > "logs/p3_m0_a6000_baseline/a6000_naive_seed${SEED}_torch27.log" 2>&1
done
```

Stats command:

```bash
python3 - <<'PY'
import pathlib, re, statistics
log_dir = pathlib.Path('/tmp/neomagi_p3_m0/parameter-golf/logs/p3_m0_a6000_baseline')
vals = []
for seed in [42, 43, 44, 45, 46]:
    text = (log_dir / f'a6000_naive_seed{seed}_torch27.log').read_text()
    vals.append(float(re.findall(r'final_int8_zlib_roundtrip_exact val_loss:[0-9.]+ val_bpb:([0-9.]+)', text)[-1]))
print(statistics.mean(vals))
print(statistics.stdev(vals))
print(statistics.pstdev(vals))
PY
```

## Procedure Inventory

| Rule | Source | P3 handling |
| --- | --- | --- |
| Record submissions live under `/records` subfolders. | upstream README "Submission Process" | M1 attempt artifacts use `records/<attempt_id>/` as the local bundle; final upstream PR naming is not hardcoded into runtime. |
| Required record files are `README.md`, `submission.json`, train log, `train_gpt.py`, and dependencies if any. | upstream README "Submission Process" | Metric Harness must mechanically check presence for P3 attempt bundles. |
| `submission.json` includes author/GitHub id, `val_bpb`, and metadata. | upstream README and `records/.../submission.json` examples | P3 `manifest.json` is a structured mirror for harness/read model; upstream `submission.json` remains procedure truth for submission-style artifact metadata. |
| Official artifact size is code bytes plus compressed model bytes, decimal `16,000,000` bytes. | upstream README FAQ | Hard Metric Harness gate. |
| Final baseline metric comes from `final_int8_zlib_roundtrip_exact ... val_bpb`. | upstream README and train logs | P3 anchor metric source. |
| Validation split is fixed first-50k-doc FineWeb validation; cached data script downloads `fineweb_val_*`. | upstream README and `data/manifest.json` | Full validation is used for baseline; future sampling would require a new baseline. |
| Submissions must provide enough logs for `p < 0.01` and at least `0.005` nat improvement over the comparison target. | upstream README "Submission Process" | Final success gate, not single-run evidence. |
| README must explain the submission in reasonable detail. | upstream README "Submission Process" | Agent writes narrative README; Metric Harness writes machine-readable manifest. |

Naming reconciliation:

- Upstream truth: `submission.json`, README, train log, runnable `train_gpt.py`, optional requirements/dependencies.
- P3 mirror: `manifest.json` for harness/read-model structure and Postgres payloads.
- M1 minimum subset: keep upstream `submission.json` plus P3 `manifest.json`; do not rename upstream procedure files.

## Result

P3-M0 baseline requirement is satisfied for the local A6000 Tier 2 anchor. M1 can use this baseline as the fixed comparison reference, while MLX smoke remains plumbing-only and non-comparable.
