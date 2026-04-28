# Fixture: `abort_during_stream`

- Status: P1-M1 playback fixture.
- Source: pi-mono `97a38bf6` `packages/agent/src/agent.ts` `abort()` + `packages/ai/src/utils/event-stream.ts`.
- Owner milestone: P1-M1 mock playback; M3 replaces the harness input with live agent events.

## Expected outline

Stream interrupted mid-text; final frame is `error` with `reason="aborted"`.
`partial` snapshot is kept on the wire so the session can persist the partial
assistant message; downstream consumers must not drop the partial content.

## Files

- `events.jsonl`: pure AssistantMessageEvent stream ending in an aborted error
  frame with partial text preserved.
- `playback.json`: sidecar control metadata that injects `abort` after event
  index 3.
