# Fixture: `usage_cache_normalization`

- Status: M0 placeholder (input + expected delivered with M2)
- Source: pi-mono `97a38bf6` architecture line 257; consumes `ai_provider.usage.normalize_provider_usage`.
- Owner milestone: M2 (provider adapter).

## Expected outline

For each provider family, raw upstream usage JSON is normalized so that:

- `input` excludes cached tokens (`prompt_tokens_details.cached_tokens` is
  subtracted before assignment);
- `cacheRead` reflects cached-token count;
- `cacheWrite` reflects cache-creation tokens (Anthropic-flavored);
- `totalTokens = input + output + cacheRead + cacheWrite`.

Lock with one raw-usage-to-normalized-usage row per provider family.
