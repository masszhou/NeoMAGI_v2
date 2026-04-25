# Fixture: `rpc_prompt_flow`

- Status: M0 core (fully delivered)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/modes/rpc/rpc-types.ts:1–264` (RpcCommand, RpcResponse), `docs/rpc.md`.
- Round-trip target: shape verification only — RPC types are not pydantic-modelled in M0;
  the fixture pins the wire layout for M5 implementation.

## Scenario

Client sends `prompt`; server responds with acceptance, then emits the agent
session events on the same stdout JSONL stream until `agent_end`.

## Files

- `input.json` — list of LF-delimited client command JSON lines.
- `expected.json` — list of expected server outputs in order: command response (success), then `AgentSessionEvent` JSON objects.

## Assertions (M5)

- First server output is a `response` for the `prompt` command with
  `success: true`. **No second `response` is emitted later** for the same
  asynchronous command — runtime outcome is reported only via events.
- Subsequent outputs match a valid `AgentSessionEvent` sequence
  (`agent_start` ... `agent_end`).
- LF-delimited framing: each line is exactly one JSON object.

In M0 only the JSON structure exists; M5 wires the actual RPC server to consume it.
