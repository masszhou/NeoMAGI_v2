"""storage — Postgres repositories, JSONL import/export, audit writer.

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §NeoMAGI Postgres Schema (line 530–627),
              §Structured Session Export (line 1064–1080).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - packages/coding-agent/src/core/session-manager.ts
  - packages/coding-agent/docs/session.md
"""
