# Fixture: `abort_during_tool`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/agent/src/agent-loop.ts` parallel-mode signal handling.
- Owner milestone: M3 (tool runtime abort).

## Expected outline

Tool execution interrupted; `tool_execution_end` carries `isError=true` with
cancellation reason. The subsequent assistant message records
`stopReason="aborted"` and the session keeps the partial state.
