Fixture: project extension command

Purpose:
- Exercises project-local `.magipi/extensions/*.py` discovery.
- Confirms a registered command can append a durable custom entry through the governed ExtensionAPI action path.

Pi reference:
- `packages/coding-agent/src/core/extensions/types.ts` `registerCommand`
- `packages/coding-agent/src/core/extensions/runner.ts`
