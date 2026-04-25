# Fixture: `compaction`

- Status: M0 core (fully delivered)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/compaction/compaction.ts:33–134` (CompactionDetails, defaults), `packages/coding-agent/src/core/session-manager.ts` (CompactionEntry).
- Round-trip target: `cli.core.session_types.SessionEntryAdapter`

## Scenario

A session with three message entries hits the compaction threshold; the loop
appends a `compaction` entry whose `firstKeptEntryId` points at the most recent
user turn.

## Files

- `input.json` — list of `SessionEntry` objects forming the pre-compaction tree.
- `expected.json` — single `CompactionEntry` produced after the compaction step.

## Assertions

- `expected.json` round-trips through `SessionEntryAdapter`.
- `timestamp` stays as ISO8601 `str` (not coerced to `datetime`).
- `firstKeptEntryId` survives alias mapping (`first_kept_entry_id` ↔ `firstKeptEntryId`).
- `details.readFiles` / `details.modifiedFiles` are preserved as lists of strings.
- The `fromHook=false` field is **kept** on the wire even though it is the default
  (Pi-compatible: do not strip booleans that wire roundtrip wants stable).
