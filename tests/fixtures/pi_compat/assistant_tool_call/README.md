# Fixture: `assistant_tool_call`

- Status: M0 placeholder (input + expected delivered with M2)
- Source: pi-mono `97a38bf6` `packages/ai/src/types.ts:247–263` (`toolcall_*` frames).
- Owner milestone: M2 (faux + real provider tool-call streaming).

## Expected outline

`toolcall_start` → `toolcall_delta` × N (cumulative arguments JSON) →
`toolcall_end` carrying the final `ToolCall` block.
