# Fixture: `rpc_sync_response`

- Status: M0 placeholder (input + expected delivered with M5)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/modes/rpc/rpc-types.ts` synchronous responses.
- Owner milestone: M5 (RPC sync command).

## Expected outline

`get_state` / `get_messages` / `get_session_stats` / `get_commands` /
`get_available_models` return their final result directly in `response.data`.
No subsequent event/message stream is required for the sync path.
