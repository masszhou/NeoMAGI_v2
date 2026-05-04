#!/usr/bin/env python3
"""Deterministic offline benchmark for the QMD autoresearch mini fixture."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/baseline.json")
    args = parser.parse_args()

    finetune_dir = Path(__file__).resolve().parent
    config_path = _resolve_path(args.config, finetune_dir)
    config = _load_json(config_path)
    seed = int(config["seed"])
    n_examples = max(1, int(config["n_examples"]))
    quality_bias = float(config.get("quality_bias", 0.0))
    evals_path = finetune_dir / str(config.get("evals_path", "evals/queries.jsonl"))
    examples = _load_jsonl(evals_path)

    rng = random.Random(seed)
    ordered = list(examples)
    rng.shuffle(ordered)
    selected = ordered[: min(n_examples, len(ordered))]

    difficulty = sum(int(item["difficulty"]) for item in selected)
    coverage = len(selected) / len(examples)
    exact_match = sum(1 for item in selected if str(item["expected_prefix"]) in {"lex:", "vec:"}) / len(selected)
    score = min(0.99, 0.35 + coverage * 0.40 + (difficulty % 11) * 0.01 + quality_bias)
    latency_ms = 120 + len(selected) * 7 + seed % 13

    print(f"METRIC score={score:.6f}")
    print(f"METRIC exact_match={exact_match:.6f}")
    print(f"METRIC latency_ms={latency_ms}")
    return 0


def _resolve_path(value: str, finetune_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate.resolve()
    return (finetune_dir / path).resolve()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"config must be a JSON object: {path}")
    return data


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        raise ValueError(f"eval set is empty: {path}")
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
