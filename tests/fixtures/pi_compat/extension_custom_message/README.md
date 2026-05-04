# Fixture: `extension_custom_message`

- Status: P1-M8 covered by runtime/unit tests; README-only golden fixture placeholder remains.
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/extensions/types.ts:1126–1130` `sendMessage` + `messages.ts` `createCustomMessage`.
- Owner milestone: P1-M8 (extension API). Machine-readable input/expected fixture deferred to the M9/M10 fixture refresh.

## Expected outline

Extension calls `send_message({customType:"foo", content:"bar", display:true})`;
this produces a `custom_message` entry rendered through the registered
renderer. Round-trip preserves `customType`, `content`, `display`, and any
`details` payload.
