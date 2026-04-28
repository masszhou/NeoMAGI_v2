# Fixture: `openai_responses_stream_tool_call`

- Status: P1-M2 provider stream regression fixture.
- Source: pi-mono `97a38bf6` OpenAI Responses stream handling for message signatures and function-call arguments.
- Owner milestone: P1-M2.

## Covered contract

`response.output_item.done` for message items preserves the Pi-compatible
`textSignature` payload, including optional `phase`.

`response.function_call_arguments.done` may carry the complete final JSON after
earlier `.delta` fragments. If the final JSON extends the accumulated fragment,
the provider emits the missing suffix as an additional `toolcall_delta` before
`toolcall_end`.
