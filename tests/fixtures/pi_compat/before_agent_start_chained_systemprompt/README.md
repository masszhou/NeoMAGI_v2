# Fixture: `before_agent_start_chained_systemprompt`

- Status: P1-M8 covered by runtime/unit tests; README-only golden fixture placeholder remains.
- Source: pi-mono `97a38bf6` architecture line 823 BeforeAgentStartEvent semantics.
- Owner milestone: P1-M8 (extension event chaining). Machine-readable input/expected fixture deferred to the M9/M10 fixture refresh.

## Expected outline

Two extensions both return a `systemPrompt` from `before_agent_start`. The
second handler sees the first replacement (chaining order = extension load
order). Multiple `message` returns accumulate into the pending list; the final
turn carries both messages.
