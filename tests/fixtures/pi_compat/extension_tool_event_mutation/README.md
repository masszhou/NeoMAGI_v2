# Fixture: `extension_tool_event_mutation`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/extensions/types.ts` `ToolCallEvent` + architecture line 825.
- Owner milestone: M3 (extension event mutation).

## Expected outline

`tool_call` handler mutates `event.input.path` in place; a later handler sees
the mutation; **no second schema validation is performed**; the final tool
`execute` receives the mutated args. Blocking still uses `{block, reason}`.
