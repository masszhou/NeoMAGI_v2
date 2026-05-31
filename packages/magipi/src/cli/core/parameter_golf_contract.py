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

VERDICT_ACCEPTED = "accepted"
VERDICT_REJECTED = "rejected"
VERDICT_ERROR = "error"

DECISION_BASELINE = "baseline"
DECISION_KEEP = "keep"
DECISION_REVERT = "revert"
DECISION_BLOCKED = "blocked"

SIGNIFICANCE_REASON_SINGLE_RUN_ONLY = "single_run_only"

ELIGIBILITY_FINAL_SIGNIFICANCE_PAYLOAD_UNEXPECTED = (
    "final_significance_payload_unexpected"
)

LINEAGE_MISSING_PARENT = "missing_parent"
LINEAGE_PARENT_NOT_IN_TASK_RUN = "parent_not_in_task_run"
LINEAGE_PARENT_CYCLE = "parent_cycle"
LINEAGE_PARENT_SELF_REFERENCE = "parent_self_reference"

TREE_DUPLICATE_ATTEMPT_ID_UNEXPECTED = "duplicate_attempt_id_unexpected"
TREE_NON_PARAMETER_GOLF_RECORD_SKIPPED = "non_parameter_golf_record_skipped"

NEXT_ACTION_RETRY_INVALID = "retry_invalid"
NEXT_ACTION_CONTINUE_FROM_BEST = "continue_from_best"
NEXT_ACTION_PROPOSE_NEXT = "propose_next"


__all__ = [
    "ANCHOR_NAME",
    "BASELINE_MEAN_VAL_BPB",
    "BASELINE_N",
    "BASELINE_SAMPLE_STD_VAL_BPB",
    "DEFAULT_REFERENCE_BUDGET",
    "DEFAULT_TIMEOUT_SECONDS",
    "METRIC_SOURCE",
    "DECISION_BASELINE",
    "DECISION_BLOCKED",
    "DECISION_KEEP",
    "DECISION_REVERT",
    "ELIGIBILITY_FINAL_SIGNIFICANCE_PAYLOAD_UNEXPECTED",
    "LINEAGE_MISSING_PARENT",
    "LINEAGE_PARENT_CYCLE",
    "LINEAGE_PARENT_NOT_IN_TASK_RUN",
    "LINEAGE_PARENT_SELF_REFERENCE",
    "NEXT_ACTION_CONTINUE_FROM_BEST",
    "NEXT_ACTION_PROPOSE_NEXT",
    "NEXT_ACTION_RETRY_INVALID",
    "REQUIRED_BUNDLE_DIRS",
    "REQUIRED_BUNDLE_FILES",
    "SIGNIFICANCE_REASON_SINGLE_RUN_ONLY",
    "SUBMISSION_ARTIFACT_CAP_BYTES",
    "TREE_DUPLICATE_ATTEMPT_ID_UNEXPECTED",
    "TREE_NON_PARAMETER_GOLF_RECORD_SKIPPED",
    "VERDICT_ACCEPTED",
    "VERDICT_ERROR",
    "VERDICT_REJECTED",
]
