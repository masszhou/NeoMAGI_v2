# Fixture: `branch_summary`

- Status: M7 follow-up playback fixture
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/compaction/branch-summarization.ts`.
- Owner milestone: M3 (branch summary).

## Expected outline

Navigation between branches inserts a `branch_summary` entry whose `details`
accumulates `readFiles` / `modifiedFiles` from the abandoned branch (sourced
from historical `read` / `edit` / `write` tool args; policy/audit wrapper must
preserve those args).

## Playback

`events.jsonl` drives the interactive event router through a branch summary
message start/end sequence without a live provider or database. The rendered
component must keep `fromId` visible so a tree switch has a readable boundary.
