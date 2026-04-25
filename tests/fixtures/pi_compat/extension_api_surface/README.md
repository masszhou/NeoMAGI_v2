# Fixture: `extension_api_surface`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/extensions/types.ts:1040–1259`.
- Owner milestone: M3 (extension API).

## Expected outline

Smoke test that every ExtensionAPI method (24 + `events: EventBus` property) is
callable on the bound runtime. Serves as parity guard for §D of
`design_docs/architecture/pi_behavior_matrix.md`. Each row matches a behavior
matrix entry; missing or renamed methods break the fixture.
