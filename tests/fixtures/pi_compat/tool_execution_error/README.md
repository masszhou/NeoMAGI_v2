# Fixture: `tool_execution_error`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/agent/src/types.ts:337–352` (`tool_execution_end`),
  architecture line 398–402 (errors → ToolResultMessage(isError=True)).
- Owner milestone: M3 (real tool runtime + policy block path).

## Expected outline

`tool_execution_start` → `tool_execution_end` with `isError=true`. Resulting
`ToolResultMessage.content` carries an LLM-readable error string; `details`
includes truncation / policy decision metadata.
