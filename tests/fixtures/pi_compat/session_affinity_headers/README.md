# Fixture: `session_affinity_headers`

- Status: M0 placeholder (input + expected delivered with M2)
- Source: pi-mono `97a38bf6` architecture line 251 + `packages/ai/src/providers/openai-completions.ts`.
- Owner milestone: M2 (provider adapter).

## Expected outline

OpenAI-compatible providers with `compat.sendSessionAffinityHeaders=true` send
`session_id`, `x-client-request-id`, `x-session-affinity` HTTP headers carrying
the cache affinity id. Header path coexists with field-based `prompt_cache_key`
when the provider supports both.
