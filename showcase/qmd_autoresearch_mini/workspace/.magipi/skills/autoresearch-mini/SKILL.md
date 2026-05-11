---
name: autoresearch-mini
description: Run the QMD autoresearch mini showcase loop with one controlled benchmark trial at a time.
---

# QMD Autoresearch Mini

## Objective
Improve `finetune/benchmark.py` or `finetune/configs/baseline.json` while keeping the run deterministic and offline.

Before acting, read `autoresearch.md`, the tail of `autoresearch.jsonl`, and `git log --oneline -5`. If the session files do not exist, call `init_experiment` first and run a baseline before changing code.

## Hypothesis
Work with one explicit hypothesis per trial. State what metric should move and why before editing.

## Files in Scope
- `finetune/benchmark.py`
- `finetune/configs/baseline.json`
- `finetune/data/`
- `finetune/evals/`
- `autoresearch.md`
- `autoresearch.sh`
- `autoresearch.jsonl`
- `autoresearch.checks.sh`

## Off-limits
- Network access, GPU assumptions, provider calls, and external package installs.
- Changes outside this showcase workspace.
- More than one benchmark-affecting idea in a single trial.
- Writing tokens or private credentials into repo files, logs, or JSONL.

## Constraints
- Keep `benchmark.py` pure stdlib, deterministic, and under two seconds.
- Preserve `METRIC name=value` output with `score` as the primary metric.
- Use `run_experiment` for benchmark execution so policy, audit, truncation, and artifacts stay governed.
- Use `log_experiment` for every baseline, kept trial, discarded trial, crash, or checks failure.
- Do not keep on `main`, `master`, or a default branch.

## Tool Use Requirements
- Do not claim that an experiment, baseline, trial, keep, discard, crash, or checks-failed path has completed unless the
  matching extension tool call has actually run.
- Use `init_experiment` to create or validate `autoresearch.md`, `autoresearch.sh`, and `autoresearch.jsonl`.
- Use `run_experiment` for every baseline and trial benchmark. Do not replace it with built-in `bash` for experiment
  execution.
- Use `trial_id="baseline"` for the baseline run. Use one explicit non-baseline trial id such as
  `trial-n-examples-5` for a config trial.
- Use `log_experiment` to persist every final status. Do not hand-write metrics into `log_experiment`; let it use the
  `run_experiment` result returned by the tool or the saved run result for that trial. Never persist `ready`; `ready`
  is only the decision point after a successful non-baseline run.
- Built-in `read` and `bash` are allowed for context checks such as reading session files or `git log`, but they are not
  substitutes for the autoresearch transaction tools.
- If `init_experiment`, `run_experiment`, or `log_experiment` are not visible as tools, stop and report blocked. Do not
  simulate results, invent metrics, or say the work is done.

## Benchmark
Baseline command:

```bash
bash autoresearch.sh
```

If `autoresearch.checks.sh` exists, it runs after a passing benchmark and can turn the run into `checks_failed`.

## Decision
Baseline logs with status `baseline`. A successful non-baseline `run_experiment` returns `status=ready`; do not pass `ready` to `log_experiment`. This is the decision point: use `keep` only for a defensible improvement, otherwise use `discard`. Use `crash` for benchmark failures and `checks_failed` for check failures. After each decision, update `autoresearch.md` with what was learned.

## Restart Note
Every `log_experiment` call must include what the next session should know: last hypothesis, result, changed files, whether the idea should be retried, and the safest next hypothesis. On restart, trust `autoresearch.md`, `autoresearch.jsonl`, and `git log` over conversation memory.
