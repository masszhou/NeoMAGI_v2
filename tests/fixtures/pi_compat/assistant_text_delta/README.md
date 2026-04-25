# Fixture: `assistant_text_delta`

- Status: M0 core (fully delivered)
- Source: pi-mono `97a38bf6` `packages/ai/src/types.ts:247–263` AssistantMessageEvent union
- Round-trip target: `ai_provider.types.AssistantMessageEventAdapter`

## Scenario

A short assistant turn that produces a single text content block via the streaming
protocol: `start` → `text_start` → `text_delta` × 2 → `text_end` → `done`.

## Files

- `events.jsonl` — one `AssistantMessageEvent` per line. Pure stream, no harness control.
- `expected.json` — final `AssistantMessage` (matches `done.message`).

## Assertions

- `start` is the first frame.
- `partial.content` accumulates text correctly across `text_delta` frames.
- `done.message` equals `expected.json` after `model_dump(by_alias=True, exclude_none=True)`.
- The `textSignature` opaque field on the final `TextContent` survives round-trip.
