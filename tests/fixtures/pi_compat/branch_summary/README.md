# Fixture: `branch_summary`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/compaction/branch-summarization.ts`.
- Owner milestone: M3 (branch summary).

## Expected outline

Navigation between branches inserts a `branch_summary` entry whose `details`
accumulates `readFiles` / `modifiedFiles` from the abandoned branch (sourced
from historical `read` / `edit` / `write` tool args; policy/audit wrapper must
preserve those args).
