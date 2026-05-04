# Fixture: `extension_api_surface`

- Status: P1-M8 covered by runtime/unit tests; README-only golden fixture placeholder remains.
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/extensions/types.ts:1040–1259`.
- Owner milestone: P1-M8 (extension API). Machine-readable input/expected fixture deferred to the M9/M10 fixture refresh.

## Expected outline

Smoke test that every ExtensionAPI method (24 + `events: EventBus` property) is
callable on the bound runtime. Serves as parity guard for §D of
`design_docs/architecture/pi_behavior_matrix.md`. Each row matches a behavior
matrix entry; missing or renamed methods break the fixture.
