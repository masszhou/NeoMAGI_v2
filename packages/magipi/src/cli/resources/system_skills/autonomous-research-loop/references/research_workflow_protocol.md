# Research Workflow Protocol Reference

Command surface: `magipi taskrun research <subcommand> [task-run-id]`.
The task-run id may be omitted inside the workspace (latest TaskRun is used).
Every command prints a JSON payload. Truth lives in Postgres task_events
(`task_research_*`) plus `records/research/<task_run_id>/` snapshots.

## Graph semantics

- Node kinds: `action` (bounded work) and `gate` (code-checked condition).
- Dependency direction: dependent -> prerequisite.
- Stored statuses: pending/claimed/running/done/failed/cancelled/superseded.
  `ready`/`blocked` are derived by code on every read; you cannot set them.
- A node is `ready` only when every dependency action is `done` and every
  dependency gate passed. `complete` requires evidence refs or a decision.
- Gates: `audit_clear`, `informed_proposal_valid`,
  `experiment_evidence_recorded`. Evaluate with
  `research gate-eval --node <gate>`; outcomes are computed from durable state.
- Restructure the graph (e.g. early stop: supersede run 2 and repoint edges)
  with `research apply --file ops.json [--dry-run]`:
  ```json
  {"ops": [
    {"op": "add_node", "node": {"node_id": "...", "kind": "action",
     "title": "...", "dependencies": ["..."]}},
    {"op": "add_dependency", "node_id": "...", "depends_on": "..."},
    {"op": "remove_dependency", "node_id": "...", "depends_on": "..."},
    {"op": "supersede_node", "node_id": "...", "reason": "..."}
  ]}
  ```
  Self-dependencies and cycles are rejected; dependency edits require the
  dependent to still be pending.

## Proposal file (`research propose --node <run-node> --file <json>`)

```json
{
  "hypothesis": "TIED_EMBED_LR=0.035 lowers val_bpb vs default under 480s",
  "base_attempt_id": null,
  "expected_metric_direction": "lower val_bpb",
  "change_summary": "change train_gpt.py TIED_EMBED_LR only",
  "run_command": "MAX_WALLCLOCK_SECONDS=480 SEED=42 .venvtorch27/bin/torchrun --nproc_per_node=1 train_gpt.py",
  "submission_files": ["train_gpt.py"],
  "risk_flags": [],
  "informed_iteration": {
    "prior_attempt_ref": "<task_experiments id of the prior attempt>",
    "observed_signal": "attempt 1 val_bpb 1.5948 vs baseline mean 1.5998",
    "failure_attribution": "delta below significance gate; single sample",
    "next_hypothesis": "...",
    "expected_effect": "...",
    "changed_from_prior": "...",
    "stop_policy_ref": "runbook stop policy: no promotion below lower_bound 0.005"
  }
}
```

- First proposal (no prior attempts in this TaskRun): `informed_iteration` may
  be omitted.
- Any later proposal: all seven `informed_iteration` fields are required and
  `prior_attempt_ref` must be a real experiment id from this TaskRun
  (see `magipi taskrun trajectory`). The command rejects violations.

## Audit + adjudication

`research audit-request` runs the auditor synchronously (default:
`claude -p --permission-mode plan --model claude-opus-4-8 --effort xhigh`),
saves `prompt.md`, `stdout.txt`, `stderr.txt`, `meta.json` under
`records/research/<id>/audits/round_NN/`, and parses the trailing fenced JSON
findings block. Audit rounds are capped (`init --round-cap`, default 3).

Adjudication file for `research adjudicate --node adjudicate_audit_1 --file`:

```json
{"entries": [
  {"finding_id": "F1", "severity": "P1", "decision": "accept",
   "rationale": "plan lacked seed pinning; fixed in proposal v2",
   "action_ref": "plan/proposal_1.json"},
  {"finding_id": "F2", "decision": "reject",
   "rationale": "auditor misread budget; 480s is the frozen tier-2 budget",
   "action_ref": "runbook §budget"}
]}
```

Rules enforced in code:

- entries must cover exactly the latest round's findings;
- P0/P1 findings keep blocking `audit_gate` regardless of your decision until
  a re-audit round no longer reports them or a human records
  `research override --finding-id <F> --approved-by <human> --reason <why>`
  (override additionally requires your prior rebuttal entry for that finding);
- after material plan revisions, re-run `audit-request` (next round).

## Execution and evidence

`research run-experiment --node <run-node> --workspace <golf-workspace>`
converts the node's latest recorded proposal into a single-attempt
`taskrun attempt-loop` run: metric harness, records bundle, seed truth,
parentage, and git closeout all apply. On success the node completes with
`task_experiments:<attempt-id>` and the records ref as evidence; on failure it
is marked failed with the loop stop reason.

Inspect evidence with:

- `magipi taskrun trajectory` — attempt tree, current best, next action;
- `magipi taskrun artifacts --verify-records` — records integrity;
- `magipi taskrun events` — full event ledger (JSONL);
- `magipi taskrun research status` — graph, gates, blockers, drive state.

## Terminal decision (`research decide`)

- Enum: `continue`, `stop_negative`, `fix_infra`, `blocked`, `success`.
- `continue`/`stop_negative`/`success` require the optimization-drive gate:
  ≥2 recorded attempts AND ≥1 structurally informed proposal.
- `stop_negative` additionally requires `--stop-policy-ref`.
- `--findings-ref` must point at an existing findings file; cite the audit
  transcript dir, adjudication JSON, attempt ids, records refs, and metrics
  inside it.
- The decision is final per workflow: a second `decide` is rejected.
