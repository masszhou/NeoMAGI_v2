# Fixture: `tool_execution_success`

- Status: M0 core (fully delivered)
- Source: pi-mono `97a38bf6` `packages/agent/src/types.ts:337–352` AgentEvent union;
  tool path matches `agent-loop.ts` parallel-mode finalizer.
- Round-trip target: `agent_core.types.AgentEventAdapter`

## Scenario

A single `read` tool call: `tool_execution_start` → `tool_execution_end` (no
streaming updates), then a `ToolResultMessage` with `isError=false`.

## Files

- `events.jsonl` — the 2-frame agent event sequence plus the resulting `ToolResultMessage` wrapped in a `message_start` / `message_end` pair (omitted from this minimal fixture; see expected.json).
- `expected.json` — final `ToolResultMessage`.

## Assertions

- `tool_execution_end.isError` is `false`.
- `tool_execution_end.result` carries the same content as the `ToolResultMessage`.
- `details.truncation` is `null` (no truncation).
- Round-trip preserves `tool_call_id` ↔ `toolCallId` alias mapping.
