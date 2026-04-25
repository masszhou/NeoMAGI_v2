# Fixture: `extension_custom_message`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/extensions/types.ts:1126–1130` `sendMessage` + `messages.ts` `createCustomMessage`.
- Owner milestone: M3 (extension API).

## Expected outline

Extension calls `send_message({customType:"foo", content:"bar", display:true})`;
this produces a `custom_message` entry rendered through the registered
renderer. Round-trip preserves `customType`, `content`, `display`, and any
`details` payload.
