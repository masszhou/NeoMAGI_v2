# Fixture: `session_before_compact_extension_replace`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/extensions/types.ts:500–508` `SessionBeforeCompactEvent`.
- Owner milestone: M3 (extension compaction override).

## Expected outline

Extension returns `{compaction: CompactionResult}` from `session_before_compact`;
Pi's default compaction is fully replaced. The follow-up `session_compact`
event carries `fromExtension=true`.
