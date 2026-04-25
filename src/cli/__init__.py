"""cli — coding-agent product layer (AgentSession + commands + tools + extensions).

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §`cli.core` Product Contract (line 404–476), Tool Registry (line 629–708),
              Extension API (line 709–836).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - packages/coding-agent/src/core/agent-session.ts
  - packages/coding-agent/src/core/session-manager.ts
  - packages/coding-agent/src/core/extensions/types.ts
  - packages/coding-agent/src/core/tools/*.ts
"""
