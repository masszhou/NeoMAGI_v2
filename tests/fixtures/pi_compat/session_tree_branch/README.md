# Fixture: `session_tree_branch`

- Status: M0 placeholder (input + expected delivered with M3)
- Source: pi-mono `97a38bf6` `packages/coding-agent/src/core/session-manager.ts` tree walk.
- Owner milestone: M3 (session tree).

## Expected outline

Tree with two siblings off a shared parent; `build_session_context(leaf)` for
each leaf returns the corresponding linear path. Used to verify the tree
serializer keeps `parentId` invariants and the JSONL projection emits entries
in stored order.
