# Fixture: `session_before_compact_extension_replace`

- Status: P1-M8 covered by runtime/unit tests; README-only golden fixture placeholder remains.
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/extensions/types.ts:500–508` `SessionBeforeCompactEvent`.
- Owner milestone: P1-M8 (extension compaction override). Machine-readable input/expected fixture deferred to the M9/M10 fixture refresh.

## Expected outline

Extension returns `{compaction: CompactionResult}` from `session_before_compact`;
Pi's default compaction is fully replaced. The follow-up `session_compact`
event carries `fromExtension=true`.
