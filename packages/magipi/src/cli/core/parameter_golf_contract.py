"""Shared P3 Mini Parameter Golf read-model constants."""

from __future__ import annotations

ANCHOR_NAME = "parameter-golf-mini"
METRIC_SOURCE = "final_int8_zlib_roundtrip_exact"
SUBMISSION_ARTIFACT_CAP_BYTES = 16_000_000
BASELINE_MEAN_VAL_BPB = 1.5997882960
BASELINE_SAMPLE_STD_VAL_BPB = 0.0023292502
BASELINE_N = 5
DEFAULT_TIMEOUT_SECONDS = 600

DEFAULT_REFERENCE_BUDGET: dict[str, object] = {
    "tier": "tier2_a6000",
    "max_wallclock_seconds": 480,
    "train_shards": 1,
    "vocab_size": 1024,
    "tokenizer_path": "./data/tokenizers/fineweb_1024_bpe.model",
    "data_path": "./data/datasets/fineweb10B_sp1024/",
    "metric_source": METRIC_SOURCE,
}

REQUIRED_BUNDLE_FILES = [
    "README.md",
    "submission.json",
    "manifest.json",
    "train_log.txt",
    "eval_result.json",
]
REQUIRED_BUNDLE_DIRS = ["submission"]


__all__ = [
    "ANCHOR_NAME",
    "BASELINE_MEAN_VAL_BPB",
    "BASELINE_N",
    "BASELINE_SAMPLE_STD_VAL_BPB",
    "DEFAULT_REFERENCE_BUDGET",
    "DEFAULT_TIMEOUT_SECONDS",
    "METRIC_SOURCE",
    "REQUIRED_BUNDLE_DIRS",
    "REQUIRED_BUNDLE_FILES",
    "SUBMISSION_ARTIFACT_CAP_BYTES",
]
