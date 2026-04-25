# Fixture: `thinking_level_change`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/session-manager.ts` ThinkingLevelChangeEntry.
- Owner milestone: M3 (thinking level).

## Expected outline

Mid-session `set_thinking_level` writes a `thinking_level_change` entry; the
new level applies to subsequent turns only.
