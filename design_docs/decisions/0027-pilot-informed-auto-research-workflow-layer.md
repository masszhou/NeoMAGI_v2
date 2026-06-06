---
doc_id: 019e9364-1e0b-73a8-a4c8-40b0c1a57f24
doc_id_format: uuidv7
doc_id_assigned_at: 2026-06-04T00:00:00Z
---
# 0027-pilot-informed-auto-research-workflow-layer

- Status: accepted
- Date: 2026-06-04
- Related:
  - `design_docs/roadmap/p3_experiment_loop_mvp.md`
  - `design_docs/decisions/0012-python-native-extension-mvp-boundary.md`
  - `design_docs/decisions/0021-workspace-materialized-skills-and-env-grants.md`
  - `design_docs/decisions/0025-use-git-as-p3-attempt-workspace-lineage.md`
  - `design_docs/decisions/0026-keep-p3-attempts-inside-one-taskrun-session.md`
  - `dev_docs/discussions/p3_m5_autonomous_research_workflow_retro.md`
  - `dev_docs/user_tests/p3_m6_magipi_autonomous_research_acceptance_runbook.md`
  - `design_docs/references/reference_beads_dependency_workflow.md`

## 选了什么

- P3-M5 CLI-core Parameter Golf loop is accepted as a deliberate design probe.
  It was allowed to exceed the earlier roadmap/ADR shape so the project could
  observe how an auto-research execution chain actually fails and succeeds.
- Design probes can inform architecture, but they do not automatically become
  the long-term product architecture.
- P3-M5 evidence is adopted as substrate evidence:
  TaskRun, `task_experiments`, `task_events`, records bundles, Git lineage,
  metric harness, seed truth, parentage, current-best derivation, and closeout
  are useful foundations for auto research.
- P3-M5 is not adopted as the full auto-research workflow. It did not prove that
  `magipi` itself can read the research protocol, propose hypotheses, request
  external audit, adjudicate audit findings, execute, and decide from evidence.
- P3-M6 introduces a separate auto-research workflow layer:

```text
skill:
  research protocol entry and model-facing operating instructions

workflow code:
  audit/adjudication gates, review-round cap, decision enum, evidence schema
  typed directed workflow state graph and dependency enforcement

TaskRun core:
  lifecycle, DB truth, permission, audit, task events, records refs

anchor harness:
  task-specific metric, budget, records verifier, artifact rules
```

- The workflow layer may be implemented with an extension, TaskRun helper, or a
  combination. The boundary is behavioral:
  - model-facing callable workflow tools may live in an extension;
  - non-bypassable truth/gate updates that own TaskRun state belong in TaskRun
    core/helpers;
  - anchor-specific metrics and records checks belong in the anchor harness.
- The workflow layer should represent the research process as a typed directed
  workflow state graph, not as prompt-only plan text:
  - action nodes perform bounded work such as read materials, propose
    hypothesis, request audit, run experiment, analyze evidence, or write
    findings;
  - gate nodes represent code-checked conditions such as no auditor P0/P1,
    records verified, metric extracted, or human override present;
  - edges express dependencies with one explicit direction; P3-M6 should use
    dependent -> prerequisite unless a later implementation ADR chooses another
    convention and updates all docs;
  - a node cannot become ready until its dependency action nodes are done and
    dependency gate nodes have passed;
  - ready/blocking state is derived from dependencies and gate outcomes; the
    model cannot write `ready=true` as truth;
  - P3-M6 may represent the graph as JSON plus TaskRun events/records; a
    dedicated graph table is deferred until the minimal path proves insufficient;
  - nodes may carry executor/tool policy contracts, but actual executor, tool,
    model, command, transcript, and elapsed time belong in TaskRun events and
    records;
  - node state must be explicit, at least pending / ready / claimed / running /
    done / blocked / failed / cancelled / superseded;
  - each completed node must leave evidence refs or a structured gate decision.
- Skill remains the research protocol entry. It is not a truth owner, not an
  executor, and not the enforcement mechanism for hard research discipline.
- Hard research discipline must be code-enforced:
  external audit transcript capture, adjudication record, auditor P0/P1 gate,
  review-round cap, final decision enum, findings/evidence schema, and
  task-specific metric/records validation.
- Claude Code or another external model can act as read-only auditor. It is not
  the controller. `magipi` must independently adjudicate audit findings before
  execution.
- If the auditor assigns P0/P1 severity, that finding blocks execution until it
  is remediated and re-reviewed, or until `magipi` records a rebuttal and a
  human explicitly approves the override. `magipi` cannot clear an auditor P0/P1
  by self-downgrading it.
- External audit runs as a read-only process outside the actor TaskRun /
  AgentSession. Its transcript is evidence attached back to the same workflow;
  it does not create a second runtime truth owner. Product wording is
  `magipi controller -> audit adapter -> Claude Code CLI`; Codex/manual shell
  remains implementation/test support, not a workflow executor role.
- P3-M6 uses a single `magipi` conductor and at most one active writing executor
  for a workflow. Independent read-only audit or analysis agents may produce
  evidence. Multiple writing agents claiming independent graph nodes is deferred
  until explicit node leases, workspace write locks, records write locks,
  heartbeat/timeout, and release/steal policy exist.
- Primary truth for P3/P3-M6 remains Postgres / TaskRun events / records bundles
  / Git lineage. Audit transcripts and findings are durable evidence artifacts
  that must reference truth; they are not independent truth sources. Neither
  skill text, extension self-report, nor auditor prose is sufficient truth by
  itself.
- This workflow graph creates procedural drive: ordering, gates, evidence, and
  non-bypassable review. It is not the optimization drive by itself. Optimization
  drive must come from proposal generation consuming prior trajectory evidence,
  strategy analysis, and stop policy.

## 为什么

- P3-M5 produced useful information precisely because it was allowed to be a
  design probe. It exposed records materialization, records ref, virtualenv
  preservation, closeout, seed truth, and proposal/metric issues that paper
  design did not fully reveal.
- Treating ADRs as an absolute ban on pilot attempts would make the project too
  conservative and slow to learn from real runs.
- Treating every successful pilot path as architecture would make the system
  drift into ad hoc CLI-core accumulation.
- The user goal is long-term auto research, not a manually operated experiment
  harness. P3-M6 must therefore prove the `magipi` user path, not only the host
  execution substrate.
- ADR-0021 already says skills are capability packages and execution still uses
  ordinary tools. That supports skill-as-protocol, not skill-as-state-machine.
- ADR-0025/0026 already establish Postgres + records + Git lineage as P3 truth
  carriers. The workflow layer should reuse these, not create another truth
  ledger.
- A state graph matches the observed research loop better than a linear
  checklist: audit can force revision and re-audit, infrastructure failure can
  branch to fix-infra then rerun plumbing, and negative evidence can terminate a
  branch without marking the whole workflow as a failure.
- A graph can make the process continue honestly, but it cannot decide that the
  next hypothesis is better. P3-M6 therefore needs an additional informed
  iteration gate outside the graph mechanics.
- Writing-agent concurrency is a separate hard problem. Without leases and
  workspace/records isolation, concurrent writers would reintroduce plan drift,
  Git lineage conflicts, and ambiguous evidence ownership.
- Beads' dependency graph is useful implementation precedent for typed edges,
  derived ready-work selection, first-class gate nodes, graph validation/apply,
  and transactional claims. It is also a warning: issue closure is not
  scientific acceptance, claims are not workspace locks, and derived-ready docs
  can drift from implementation unless tested.

## 放弃了什么

- 方案 A：continue expanding P3-M5 CLI-core into the full research conductor.
  - 放弃原因：it would mix anchor harness, workflow policy, audit/adjudication,
    findings generation, and TaskRun truth into one hard-coded path.
- 方案 B：put the full workflow discipline into skill text.
  - 放弃原因：skills are prompt/context, not reliable enforcement. Models can
    skip, forget, or reinterpret prompt-only gates.
- 方案 C：make extension the sole truth owner for auto research.
  - 放弃原因：extensions are useful model-facing tools and lifecycle hooks, but
    P3 truth must remain in Postgres / records / TaskRun events / Git lineage;
    transcripts are evidence artifacts, not a separate truth ledger.
- 方案 D：require a candidate pool or genetic-search engine in P3-M6.
  - 放弃原因：population-style proposal generation is promising but still a
    discussion topic. P3-M6 only needs one bounded hypothesis workflow.
- 方案 E：let Claude Code audit directly control the plan.
  - 放弃原因：auditor findings are evidence. The controlling decision must be
    `magipi` adjudication with reasons and references.
- 方案 F：allow multiple writing agents to claim independent graph nodes in P3-M6.
  - 放弃原因：read-only parallel work is useful, but concurrent workspace writers
    need leases, write locks, and ownership semantics that are outside M6.
- 方案 G：adopt Beads/Dolt as the P3-M6 workflow engine.
  - 放弃原因：Beads is a useful dependency-graph reference, but `magipi` truth is
    already Postgres / TaskRun events / records / Git lineage, and research
    acceptance requires audit and metric semantics beyond issue closure.

## 影响

- P3-M6 implementation should start with a default or workspace-materialized
  research skill plus code-enforced audit/adjudication helpers.
- The P3-M5 Parameter Golf code should be retained and narrowed as anchor
  substrate: attempt execution, metric harness, records verifier, and trajectory
  support.
- P3-M6 acceptance must not pass from Codex/manual shell execution alone. It must
  exercise the `magipi` workflow path.
- Implementation planning must decide which M6 helpers are extension tools and
  which are TaskRun/core helpers. That decision should be based on whether the
  operation is model-facing and callable, or whether it owns non-bypassable
  TaskRun truth/gates.
- Implementation must maintain a typed workflow state graph or an equivalent
  TaskRun/event/records representation that can be reconstructed. Code must
  enforce dependency readiness, valid node transitions, and gate outcomes.
- Minimal M6 storage should start with JSON graph snapshots plus TaskRun events
  and records evidence. Add dedicated graph tables only if this path fails.
- Implementation should include a graph-apply boundary: validate/dry-run the
  planned nodes and dependency edges, reject self-dependencies/cycles for
  blocking edges, then persist the accepted graph update as TaskRun truth.
- Implementation should keep blocking dependency edges minimal at first and keep
  non-blocking evidence/review/supersedes links out of ready-work selection.
- Implementation must not treat workflow completion as optimization success.
  M6 acceptance also needs evidence that a later proposal consumed prior metric
  / verdict evidence and changed strategy or satisfied a stop policy.
- Audit/adjudication artifacts must be directly referenceable from findings and
  should be durable enough to survive context compaction and restart.
- Implementation must preserve the auditor P0/P1 override rule: `magipi` may
  rebut an auditor P0/P1, but execution cannot proceed unless the issue is fixed
  and re-reviewed or a human explicitly approves the override.
- M6 may permit parallel read-only audit/analysis evidence. It must not permit
  multiple writing agents to mutate the same workflow workspace or records until
  a later ADR defines leases and isolation.
- If the host-command audit record still labels host-owned commands as
  `actor="extension"`, track it as an implementation follow-up because it
  confuses extension runtime participation with host-command audit origin.
- Future design probes are allowed, but findings must label them as probes and
  state which parts are substrate evidence versus architecture commitments.
