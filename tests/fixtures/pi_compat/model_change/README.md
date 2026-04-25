# Fixture: `model_change`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/session-manager.ts` ModelChangeEntry.
- Owner milestone: M3 (model switching).

## Expected outline

Mid-session `set_model` writes a `model_change` entry; `build_session_context`
reflects the latest model state without rewriting prior assistant messages.
