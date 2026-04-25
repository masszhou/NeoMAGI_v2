# Fixture: `abort_during_stream`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/agent/src/agent.ts` `abort()` + `packages/ai/src/utils/event-stream.ts`.
- Owner milestone: M3 (agent loop abort).

## Expected outline

Stream interrupted mid-text; final frame is `error` with `reason="aborted"`.
`partial` snapshot is kept on the wire so the session can persist the partial
assistant message; downstream consumers must not drop the partial content.
