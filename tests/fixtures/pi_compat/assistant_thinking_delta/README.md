# Fixture: `assistant_thinking_delta`

- Status: P1-M1 playback fixture; P1-M2 provider thinking semantics are implemented in `faux` / Anthropic stream tests.
- Source: pi-mono `97a38bf6` `packages/ai/src/types.ts:247–263` (`thinking_*` frames).
- Owner milestone: P1-M1 / P1-M2.

## Expected outline

`thinking_start` → `thinking_delta` × N → `thinking_end` followed by a `done` frame.
The final `AssistantMessage.content` carries one `ThinkingContent` block with
`thinkingSignature` opaque field preserved.

## Files

- `events.jsonl`: pure AssistantMessageEvent playback stream consumed by the M1
  router/playback harness.
