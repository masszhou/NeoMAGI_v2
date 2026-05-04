# QMD Autoresearch Mini Fixture

This directory is a deterministic, dependency-light stand-in for QMD's
`finetune/` tree. It is intentionally small enough for CI while preserving the
shape that the autoresearch extension expects:

- `configs/baseline.json` controls the run.
- `evals/queries.jsonl` is the tiny offline evaluation set.
- `benchmark.py` prints stable `METRIC name=value` lines.

Run from the showcase workspace:

```bash
python finetune/benchmark.py --config finetune/configs/baseline.json
```

Run from this directory:

```bash
python benchmark.py --config configs/baseline.json
```
