# Fixture: `overflow_error_patterns`

- Status: M0 core (fully delivered)
- Source: pi-mono `97a38bf6` `packages/ai/src/utils/overflow.ts:28–49` OVERFLOW_PATTERNS,
  `60–64` NON_OVERFLOW_PATTERNS.
- Round-trip target: `ai_provider.overflow.is_context_overflow`.

## Scenario

Each provider family produces a different overflow error string. Pi's pattern
set must classify them all as overflow, and the throttling exclusion set must
prevent the generic `too many tokens` fallback from misclassifying transient
capacity errors.

## Files

- `input.json` — `{overflow_samples, non_overflow_samples}`.
- `expected.json` — for each sample, the expected boolean result of
  `is_context_overflow`.

## Assertions

- 17 overflow sample messages → `True`.
- 4 non-overflow samples (`Throttling error:`, `Service unavailable:`, generic
  `rate limit`, `429`) → `False`.

These samples are also exercised in `tests/test_overflow.py`; the fixture is the
shared evidence base for cross-language port (TypeScript pi-mono + Python
NeoMAGI must classify the same way).
