# Fixture: `session_affinity_headers`

- Status: P1-M2 provider-cache contract covered by `openai_completions_prompt_cache/fixture.json`.
- Source: pi-mono `97a38bf6` architecture line 251 + `packages/ai/src/providers/openai-completions.ts`.
- Owner milestone: P1-M2.

## Expected outline

OpenAI-compatible providers with `compat.sendSessionAffinityHeaders=true` send
`session_id`, `x-client-request-id`, `x-session-affinity` HTTP headers carrying
the cache affinity id. Header path coexists with field-based `prompt_cache_key`
when the provider supports both.

This directory is kept as the named compatibility scene. The machine-readable
assertion lives in `openai_completions_prompt_cache/fixture.json` under
`compatibleProvider.expectedHeaders` to avoid duplicating the same provider
payload fixture.
