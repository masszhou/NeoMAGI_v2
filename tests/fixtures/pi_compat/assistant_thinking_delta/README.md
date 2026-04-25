# Fixture: `assistant_thinking_delta`

- Status: M0 placeholder (input + expected delivered with M2)
- Source: pi-mono `97a38bf6` `packages/ai/src/types.ts:247–263` (`thinking_*` frames).
- Owner milestone: M2 (real provider thinking stream).

## Expected outline

`thinking_start` → `thinking_delta` × N → `thinking_end` followed by a `done` frame.
The final `AssistantMessage.content` carries one `ThinkingContent` block with
`thinkingSignature` opaque field preserved.
