---
doc_id: 019e9c9d-388b-737a-8ba3-f000d277a2f0
doc_id_format: uuidv7
doc_id_assigned_at: 2026-06-06T11:06:00Z
---
# Beads Dependency Workflow: P3-M6 Design Reference

## 状态

- Status: accepted reference
- Date: 2026-06-06
- Source: [gastownhall/beads](https://github.com/gastownhall/beads), reviewed at
  commit `0da7f51`
- Related:
  - `design_docs/decisions/0027-pilot-informed-auto-research-workflow-layer.md`
  - `design_docs/roadmap/p3_experiment_loop_mvp.md`
  - `dev_docs/user_tests/p3_m6_magipi_autonomous_research_acceptance_runbook.md`

Key source files reviewed:

- [`docs/DEPENDENCIES.md`](https://github.com/gastownhall/beads/blob/0da7f51/docs/DEPENDENCIES.md)
  for dependency direction, blocking types, ready-work semantics, and gates.
- [`docs/MOLECULES.md`](https://github.com/gastownhall/beads/blob/0da7f51/docs/MOLECULES.md)
  for graph workflow conventions and temporal-language pitfalls.
- [`cmd/bd/graph_apply.go`](https://github.com/gastownhall/beads/blob/0da7f51/cmd/bd/graph_apply.go)
  for symbolic graph validate/apply.
- [`internal/storage/issueops/claim.go`](https://github.com/gastownhall/beads/blob/0da7f51/internal/storage/issueops/claim.go)
  for transactional claim behavior.
- [`internal/storage/issueops/ready_work.go`](https://github.com/gastownhall/beads/blob/0da7f51/internal/storage/issueops/ready_work.go)
  and [`cmd/bd/protocol/blocked_status_test.go`](https://github.com/gastownhall/beads/blob/0da7f51/cmd/bd/protocol/blocked_status_test.go)
  for derived ready/blocked behavior.
- [`docs/EXCLUSIVE_LOCK.md`](https://github.com/gastownhall/beads/blob/0da7f51/docs/EXCLUSIVE_LOCK.md)
  for lock limitations.

## 0. What Beads Shows

Beads is a reference, not a dependency adoption. MagiPI should not import Beads
or Dolt for P3-M6.

Useful implementation facts:

```text
bd dep add issue-2 issue-1
```

- Edge direction is **dependent -> prerequisite**: `issue-2` depends on
  `issue-1`; `issue-1` blocks `issue-2`.
- Blocking links affect `ready`; non-blocking links are traceability only.
- `ready` / `blocked` is derived from dependencies and gate state, not manually
  asserted by the agent.
- Blocking self-dependencies and cycles are rejected at write time.
- Gates are first-class nodes for external conditions such as PR, CI, timer,
  upstream work, or human approval.
- `bd create --graph` provides a useful graph validate/apply pattern.
- Claim is transactional, but it is not a workspace, records, or Git lock.
- Beads docs/implementation drift around derived blocking is a warning: any
  materialized ready/blocked state needs recompute tests.

## 1. Direct Guidance For MagiPI

P3-M6 should use a small JSON workflow graph plus TaskRun events/records, not a
new workflow engine.

```text
run_experiment depends_on audit_gate
audit_gate depends_on request_audit
```

Design rules:

- Use dependent -> prerequisite as the only blocking edge direction.
- Store executor/tool requirements on graph nodes as policy contracts.
- Store actual executor/tool/model/command/transcript in TaskRun events and
  records, not in the node contract.
- Make audit, adjudication, and audit-gate separate nodes.
- Use product wording: `magipi controller -> audit adapter -> Claude Code CLI`.
  Codex/manual shell is implementation/test support, not a product executor.
- Derive readiness in code; never accept model-written `ready=true`.
- Keep one writing executor in M6. Parallel read-only review is allowed.
- Treat scientific acceptance separately from node completion.
