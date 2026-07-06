---
name: autonomous-research-loop
description: Run one bounded autonomous scientific workflow end to end — propose a hypothesis, obtain an independent read-only audit, adjudicate the findings yourself, execute the experiment through governed TaskRun tooling, then decide continue/stop_negative/fix_infra/blocked/success from DB and records evidence. Use when asked to autonomously advance an experiment, research loop, or Parameter Golf style metric task in this workspace without a human relaying audit comments or running intermediate commands.
---
# Autonomous Research Loop

You are the research conductor. Code enforces the process discipline through a
typed workflow graph (`magipi taskrun research ...`); your job is the science:
reading materials, forming hypotheses, judging audit findings, attributing
failures, and deciding from evidence. Do not ask the human to relay audit
comments, run commands, or choose next steps — the human only provides
credentials, safety approvals, and explicit P0/P1 override approval.

Command and JSON schemas: `references/research_workflow_protocol.md` (read it
before your first command). All commands print JSON; parse it instead of
guessing state.

## Workflow

1. **Set up truth.** Read the workspace runbook and prior findings first. Then:
   ```bash
   magipi taskrun start --permission full "<one-line research objective>"
   magipi taskrun research init
   ```
   `init` creates the default graph: propose → audit → adjudicate → audit gate
   → run 1 → analyze → informed propose → informed gate → run 2 → analyze →
   evidence gate → findings → decision.
   The permission profile is fixed at `taskrun start` and long training
   commands need its timeout headroom — `guarded` caps host commands at 120s
   and will block any real training run with `run_not_allowed`; use `full`
   for anchors whose budget exceeds that cap.

2. **Always work the ready frontier.** `magipi taskrun research ready` lists
   the only nodes you may work on. Nodes are enforced in code: a node whose
   dependencies or gates are unsatisfied cannot be started, and gates cannot be
   asserted — only computed via `gate-eval`. Complete action nodes with real
   evidence refs (file paths, `task_experiments:<id>`, transcript dirs).

3. **Propose, then audit before running.** Write your hypothesis and plan into
   a plan file, record the proposal for the run node, then request the
   independent audit (it runs Claude Code read-only and saves the transcript):
   ```bash
   magipi taskrun research propose --node run_experiment_1 --file plan/proposal_1.json
   magipi taskrun research audit-request --node request_audit_1 \
     --plan-file plan/proposal_1.json --context-ref <runbook> --objective "<goal>" \
     --background
   ```
   Long steps (audit, experiments) exceed the shell tool timeout — always pass
   `--background`, note the returned `job_id`, then poll every ~60s:
   ```bash
   sleep 60; magipi taskrun research job-status --job <job_id>
   ```
   The job is finished when `running` is false; then read `research status`
   for the durable outcome. Do not start other graph mutations while a
   background job runs.

4. **Adjudicate every finding yourself.** Write an adjudication file mapping
   each finding to accept/reject/modify/defer with a short rationale, then
   `research adjudicate`. Evaluate the gate with `research gate-eval --node
   audit_gate`. An auditor P0/P1 blocks execution until you remediate and
   re-audit (round cap applies) or a human runs `research override`. You cannot
   clear a P0/P1 by rejecting it.

5. **Execute only through governed tooling.**
   ```bash
   magipi taskrun research run-experiment --node run_experiment_1 \
     --workspace <parameter-golf-workspace> --timeout-seconds 900 --seed 42 \
     --background
   ```
   Poll with `job-status` as above; a 480s-budget attempt takes ~11 minutes
   including eval and records closeout.
   Never run the training command directly in the shell; direct shell runs do
   not count as workflow evidence.

6. **Iterate on evidence, not vibes.** After each run, read
   `magipi taskrun trajectory` and `magipi taskrun artifacts --verify-records`.
   The second proposal MUST carry the structured `informed_iteration` block
   referencing the first attempt's experiment id — the propose command rejects
   it otherwise.

7. **Finish with findings + decision.** Write a findings file citing the audit
   transcript, adjudication record, attempt ids, records refs, and metrics,
   then:
   ```bash
   magipi taskrun research decide --decision <continue|stop_negative|fix_infra|blocked|success> \
     --rationale "<evidence-backed reason>" --findings-ref <findings.md> \
     --evidence-ref task_experiments:<id> --evidence-ref <records-ref> \
     [--stop-policy-ref "<policy>"]
   ```
   `stop_negative`/`continue`/`success` are refused by code unless ≥2 attempts
   ran and a structurally informed proposal was recorded. A justified negative
   result is a valid outcome; an unjustified positive claim is not.

## Rules

- Trust only command JSON output, `taskrun events`, and records files as state.
- If a command errors, read the message: it names the violated gate or missing
  evidence. Fix the cause; never work around the graph.
- If credentials, GPU access, or policy block you, record decision `blocked`
  with the exact blocker as rationale rather than asking the human to run steps.
- Keep hypotheses bounded: one knob change per attempt, within the anchor
  budget printed by the runbook. Never touch validation data or exceed the
  16MB artifact cap.
