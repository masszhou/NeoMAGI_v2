"""cli.core — AgentSession lifecycle, session entry types, compaction, commands.

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §`cli.core` Product Contract (line 404–476),
              §Durable Session Architecture (line 478–528),
              §Compaction and Branch Summary (line 979–1025).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - packages/coding-agent/src/core/agent-session.ts
  - packages/coding-agent/src/core/session-manager.ts
  - packages/coding-agent/src/core/messages.ts
  - packages/coding-agent/src/core/slash-commands.ts
  - packages/coding-agent/src/core/compaction/*.ts
"""
