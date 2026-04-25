# Fixture: `before_agent_start_chained_systemprompt`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` architecture line 823 BeforeAgentStartEvent semantics.
- Owner milestone: M3 (extension event chaining).

## Expected outline

Two extensions both return a `systemPrompt` from `before_agent_start`. The
second handler sees the first replacement (chaining order = extension load
order). Multiple `message` returns accumulate into the pending list; the final
turn carries both messages.
