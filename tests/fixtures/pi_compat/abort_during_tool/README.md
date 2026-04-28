# Fixture: `abort_during_tool`

- Status: P1-M1 playback fixture.
- Source: pi-mono `97a38bf6` `packages/agent/src/agent-loop.ts` parallel-mode signal handling.
- Owner milestone: P1-M1 mock playback; M3 replaces the harness input with live tool runtime events.

## Expected outline

Tool execution is interrupted after a `tool_execution_update`. The fixture does
not include `tool_execution_end`; `playback.json` injects abort after event
index 1 and the controller keeps the partial tool result visible while returning
the editor to idle.

## Files

- `events.jsonl`: AgentEvent stream with `tool_execution_start` and one partial
  `tool_execution_update`.
- `playback.json`: sidecar control metadata that injects `abort` after event
  index 1.
