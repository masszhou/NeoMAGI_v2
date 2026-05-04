# Fixture: `extension_tool_event_mutation`

- Status: P1-M8 covered by runtime/unit tests; README-only golden fixture placeholder remains.
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/extensions/types.ts` `ToolCallEvent` + architecture line 825.
- Owner milestone: P1-M8 (extension event mutation). Machine-readable input/expected fixture deferred to the M9/M10 fixture refresh.

## Expected outline

`tool_call` handler mutates `event.input.path` in place; a later handler sees
the mutation; **no second schema validation is performed**; the final tool
`execute` receives the mutated args. Blocking still uses `{block, reason}`.
