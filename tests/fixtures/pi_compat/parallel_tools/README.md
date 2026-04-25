# Fixture: `parallel_tools`

- Status: M0 core (fully delivered)
- Source: pi-mono `97a38bf6` `packages/agent/src/agent-loop.ts` parallel mode (preflight sequential, execute concurrent, emit `tool_execution_end` as tools finish, then emit `toolResult` messages **in assistant source order**).
- Round-trip target: `agent_core.types.AgentEventAdapter`

## Scenario

The assistant requested two tool calls in one message: `read("a.py")` and
`grep("foo", "src/")`. They run concurrently; `grep` finishes first, but the
two `toolResult` messages are emitted in source order (`read` then `grep`).

## Files

- `events.jsonl` — the parallel sequence (`tool_execution_start` × 2, then `tool_execution_end` for `grep` first, then `read`).
- `expected.json` — list of two `ToolResultMessage` objects in assistant source order.

## Assertions

- Two `tool_execution_start` frames precede any `tool_execution_end`.
- `tool_execution_end` ordering reflects real completion order (here `grep` first).
- Final `toolResult` messages emerge in assistant source order, not completion order.
