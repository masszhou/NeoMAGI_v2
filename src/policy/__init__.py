"""policy — path / shell / network / memory permission evaluation and sandbox adapters.

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §Tool Registry, Policy, Sandbox, Audit (line 629–708).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - NeoMAGI-only layer; Pi has implicit per-tool checks but no unified policy module.
"""
