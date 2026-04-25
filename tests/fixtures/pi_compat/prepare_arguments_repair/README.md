# Fixture: `prepare_arguments_repair`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/extensions/types.ts:390–447` `ToolDefinition.prepareArguments`.
- Owner milestone: M3 (tool argument repair).

## Expected outline

`prepareArguments` rewrites a non-canonical LLM argument shape (e.g. a string
where the schema expects a list); the rewritten args still pass schema
validation. The hook exists to repair model output, not to bypass validation.
