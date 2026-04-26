# Fixture: `usage_cache_normalization`

- Status: M2 provider usage fixture.
- Source: pi-mono `97a38bf6` architecture line 257; consumes typed
  `ai_provider.usage.normalize_*_usage` functions.
- Owner milestone: M2 (provider adapter).

## Expected outline

For each provider family, raw upstream usage JSON is normalized so that:

- `input` excludes cached tokens (`prompt_tokens_details.cached_tokens` is
  subtracted before assignment);
- `cacheRead` reflects cached-token count;
- `cacheWrite` reflects cache-creation tokens (Anthropic-flavored);
- `totalTokens = input + output + cacheRead + cacheWrite`.

Locked rows:

- `anthropic.json`
- `openai_responses.json`
- `openai_completions.json`
- `openai_compatible_cache_write.json`
