"""ai_provider — Pi-compatible message / content / stream / model / provider types.

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §`ai_provider` Protocol (line 96–280).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - packages/ai/src/types.ts
  - packages/ai/src/stream.ts
  - packages/ai/src/utils/event-stream.ts
  - packages/ai/src/utils/overflow.ts
  - packages/ai/src/providers/{anthropic,openai-responses,openai-completions,amazon-bedrock,faux}.ts
"""
