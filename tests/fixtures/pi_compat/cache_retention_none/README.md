# Fixture: `cache_retention_none`

- Status: M0 core (fully delivered)
- Source: pi-mono `97a38bf6` architecture line 249: `cacheRetention == "none"` disables all provider cache/session propagation.
- Round-trip target: `ai_provider.types.AssistantMessageAdapter` for the response;
  `input.json` is plain JSON for adapter assertions in M2.

## Scenario

Provider stream invoked with `cacheRetention="none"`. Adapter must:

- omit `sessionId` / `prompt_cache_key` / `cache_control` / `cachePoint` /
  `prompt_cache_retention` from outgoing payloads;
- omit affinity headers (`session_id`, `x-client-request-id`,
  `x-session-affinity`).

The response carries usage with **zero `cacheRead` / `cacheWrite`** (no cache hit
because the adapter disabled cache propagation upstream).

## Files

- `input.json` — `{stream_options, outgoing_payload, outgoing_headers}` snapshot
  asserting the absence of cache fields.
- `expected.json` — `AssistantMessage` with normalized usage (cache fields zero).

## Assertions

- `outgoing_payload` does not contain any of: `sessionId`, `prompt_cache_key`,
  `cache_control`, `cachePoint`, `prompt_cache_retention`.
- `outgoing_headers` does not contain any of: `session_id`,
  `x-client-request-id`, `x-session-affinity`.
- Response `usage.cacheRead == 0` and `usage.cacheWrite == 0` and the
  corresponding cost rows are zero.
- `responseId` opaque field still passes through round-trip.
