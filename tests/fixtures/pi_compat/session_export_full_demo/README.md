# Session Export Full Demo

- Owner milestone: P1-M10.
- Purpose: deterministic structured export fixture subset covering full-tree entries, active-branch projection, tool metadata, usage/cost, redaction, compaction, and branch summary.
- The executable setup lives in `tests/cli/core/test_session_export.py`; `expected_subset.json` keeps the acceptance assertions machine-readable without freezing volatile UUIDs.
