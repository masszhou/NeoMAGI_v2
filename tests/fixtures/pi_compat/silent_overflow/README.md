# Fixture: `silent_overflow`

- Status: M0 core (fully delivered)
- Source: pi-mono `97a38bf6` `packages/ai/src/utils/overflow.ts:122–128` silent-overflow branch (z.ai style); architecture line 262.
- Round-trip target: `ai_provider.types.AssistantMessageAdapter` + `is_context_overflow`.

## Scenario

Provider returns `stopReason="stop"` and a successful response, but
`usage.input + usage.cacheRead > model.contextWindow`. The detector must flag
this as overflow when `contextWindow` is supplied.

## Files

- `input.json` — `{message, contextWindow}`.
- `expected.json` — `{overflow: true, fallbackOverflowWithoutContextWindow: false}`.

## Assertions

- `is_context_overflow(message, contextWindow=128000)` → `True`.
- `is_context_overflow(message)` (without `contextWindow`) → `False`.
- The message round-trips through `AssistantMessageAdapter` byte-stably.
