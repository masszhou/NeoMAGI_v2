# Fixture: `anthropic_cache_long`

- Status: M2 provider-cache fixture.
- Purpose: Direct Anthropic `long` retention maps to ephemeral `cache_control`
  with `ttl: 1h`; proxy base URLs omit `ttl`.

