---
doc_id: 019e7a01-86b1-7401-aa89-eedfa41bcdbb
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-30T16:53:00+00:00
status: accepted
date: 2026-05-30
---
# P3-M1 实现计划：Single-Attempt Closed Loop

- 状态：accepted
- 日期：2026-05-30
- 路线图：`design_docs/roadmap/p3_experiment_loop_mvp.md` (§ P3-M1)
- 架构文档：`design_docs/architecture/p3_experiment_loop_architecture.md`
- 预算参考：`design_docs/references/reference_mini_parameter_golf_budget.md`
- 数据模型：`design_docs/data_models/task_experiments.md`
- 前置 closeout：
  - `dev_docs/logs/p3_m0_anchor_setup_closeout.md`
  - `dev_docs/logs/p3_m0_anchor_baseline_findings.md`
- P2 参考：
  - `dev_docs/plans/p2_m6_experiment_benchmark_loop.md`
  - `packages/magipi/src/cli/core/taskrun_experiment_attempts.py`
  - `packages/magipi/src/cli/core/taskrun_experiment_loop.py`
  - `packages/magipi/src/cli/core/taskrun_experiment_trial.py`
  - `packages/magipi/src/cli/core/taskrun_experiments.py`
  - `packages/magipi/src/cli/core/taskrun_host_audit.py`
  - `packages/magipi/src/cli/core/taskrun_workspace_state.py`
  - `packages/magipi/src/storage/taskrun_repository.py`
  - `tests/cli/core/test_taskrun_experiments.py`
  - `tests/storage/test_taskrun_repository.py`

## 目标

完成 P3-M1：在 Mini Parameter Golf anchor 上跑通一次完整 attempt 的闭环：

```text
hypothesis -> config diff -> run -> metric extraction -> artifact bundle -> verdict -> task_experiments record
```

M1 的验收是“闭环真实可执行且 evidence 合规”，不是“超过 baseline”。单次 candidate 只能产生
single-run evidence，不能给出最终 p<0.01 success verdict。

M1 结束时必须能回答：

```text
1. 一个 agent / operator 如何在一个 TaskRun 内发起并收口一次 Mini Parameter Golf attempt？
2. attempt 的 hypothesis、config、run command、metric、artifact、verdict 分别落在哪里？
3. records/<attempt_id>/ 是否包含 M0 冻结的最小合规 bundle？
4. Metric Harness 是否机械校验 budget、val_bpb、submission artifact size 和 required files？
5. task_experiments 是否能用现有 JSONB payload 表达 M1 evidence，且不引入 schema migration？
```

## 关键口径

### M1 是单 attempt 闭环，不是 autonomous loop

M1 可以由人工触发、人工给定候选 hypothesis 或让当前 TaskRun agent 做一次 bounded step。它只要求一次
attempt 从开始到 verdict 收口，并把 evidence 写入 ledger 和 records bundle。

M1 不实现多 attempt 策略、自动 propose-next、连续停止条件、critic checkpoint 或 final significance session。
这些分别属于 P3-M3 / M5。

### 复用 P2 TaskRun 和 task_experiments

M1 不创建第二套 experiment ledger，也不把 attempt 建成新的 TaskRun：

- `1 Experiment Session = 1 TaskRun = 1 long-lived AgentSession`；
- `Attempt = task_experiments record + records/<attempt_id>/ + optional scoped git commit`；
- root / single attempt 的 parent 可以先写入 `diff_ref.parent_experiment_id = null`；
- artifact metadata、records ref、commit ref、verdict status 先放 JSONB payload；
- schema promotion、indexes、read model 由 P3-M3 决定。

### Metric Harness 负责机器判定

LLM 可以写 hypothesis、README 和 change rationale，但不能单独决定 metric / verdict。M1 的 verdict 至少由
确定性 harness 检查：

- `val_bpb` 来自 final exact metric log line；
- candidate 使用 M0 冻结的 Tier 2 A6000 budget；
- `MAX_WALLCLOCK_SECONDS=480`，executor timeout 必须覆盖 480s budget，并服从所选执行路径的 hard cap；
- `train-shards=1`、`VOCAB_SIZE=1024`、sp1024 tokenizer / data path 与 M0 一致；
- submission artifact size `<= 16,000,000` decimal bytes；
- `records/<attempt_id>/` 必需文件存在且 manifest 可解析。

### Verdict 是 M1 evidence verdict，不是 P3 final success

M1 canonical verdict 写在 `task_experiments.result.verdict.status`：

```text
accepted = 单次 evidence valid，且相对 baseline mean 有改善；不代表最终显著成功，
           也可能仍落在噪声范围内
rejected = 单次 evidence valid，但未改善 / 超 cap / budget 不可比 / reproduction gate fail
error    = run 或 harness 失败，无法形成有效 candidate evidence
```

P2 兼容字段 `decision` 暂按 M0 closeout 映射：

```text
accepted -> keep
rejected -> revert 或 blocked
error    -> blocked
```

如果 M1 不做自动 revert，`rejected` 默认映射 `blocked`，并在 reason 中说明 workspace 需要人工处置。

M1 写入 `task_experiments.decision` 时必须严格限于 P2 现有四值：

```text
baseline | keep | revert | blocked
```

`accepted / rejected / error` 只能写入 `result.verdict.status`。现有事件映射和 summary reducer 都假设
`decision` 是 P2 词表；M1 不能把 P3 verdict status 直接写入 `decision` 列。

### Procedure 消费先做最小真实路径

M1 只把 Parameter Golf 上游约定落实到 anchor-specific harness / docs / command wrapper，不把规则硬编码进
agent_core 或通用 TaskRun runtime 常量。未来 `parameter-golf-conventions` skill 可沉淀 procedure 阅读方法，
但 M1 不要求先创建 skill。

## 分阶段策略

| Phase | 目标 | 可独立验收点 |
| --- | --- | --- |
| M1a | Contract refresh + entrypoint choice | 确认 M1 不需要 schema migration；确定 CLI / helper 入口和现有 P2 seam |
| M1b | Attempt bundle contract | `records/<attempt_id>/manifest.json` schema、required files、submission artifact size 计算口径就位 |
| M1c | Metric Harness | 可从 train log / manifest 机械校验 val_bpb、budget、artifact cap、required files |
| M1d | Single-attempt executor | 一次 attempt 可执行 train/eval、收集 log、生成 bundle、写 task_experiments |
| M1e | Git lineage evidence | 可选创建 scoped commit / diff ref；失败不影响 Postgres evidence truth |
| M1f | Tests + manual A6000 smoke | 单元测试覆盖 parser / manifest / ledger；A6000 手测跑一次真实 attempt |
| M1g | Closeout | closeout、manual findings、progress 更新；明确 M2/M3 deferred |

## 范围

### In scope

- 新增或扩展一个 P3 anchor-specific single-attempt 入口，优先挂在现有 TaskRun CLI / core seam 下；
- 在一个已存在或新建 TaskRun 中记录一次 Mini Parameter Golf attempt；
- 生成 `records/<attempt_id>/` 最小 artifact bundle：
  - `README.md`
  - `submission.json`
  - `manifest.json`
  - `train_log.txt`
  - `eval_result.json`
  - `submission/` directory with the capped submission artifact
- 解析 final exact `val_bpb`；
- 计算 submission artifact bytes，并校验 16,000,000 cap；
- 校验 M0 冻结 budget 与 data/tokenizer refs；
- 把 metric、artifact metadata、verdict、records ref 写入 `task_experiments` JSONB；
- 在 `task_events` 中记录 summary-grade lifecycle evidence；
- focused tests 覆盖 harness、payload shape、repository append/list；
- 一次真实 A6000 single-attempt smoke，若 reference budget 资源不可用则记录 blocked / skipped 条件。

### Out of scope

- 不实现 autonomous multi-attempt loop；
- 不实现 final significance session 或 Welch t-test verdict；
- 不升级到 Tier 3 / H100，不改变 M0 budget；未来 Tier 3 需要重建 baseline 后才可比较。
- 不新增 schema migration、artifact table 或 WebUI read model；
- 不实现 Execution Narrative Renderer；
- 不实现 critic agent；
- 不实现 browser write path、Gateway、channel、room、control plane、多机器绑定；
- 不把 artifact bytes 存进 Postgres 或 WebUI 内存；
- 不把 upstream parameter-golf 规则写入 `agent_core` protocol。

## 设计约束

### Attempt id 与 records ref 必须稳定

M1 需要确定 attempt id 生成策略，并保证它能在三处互相校验：

```text
task_experiments.id
task_experiments.diff_ref.records_ref
records/<attempt_id>/manifest.json.attempt_id
```

如果现有 repository id 只能在 append 后获得，executor 应先创建 staging dir，再在 append 后原子移动 / 重命名到
最终 `records/<attempt_id>/`，或使用 deterministic client-side attempt id。不得留下“DB id 与 records dir
不一致但靠 README 解释”的状态。

### 每个 attempt 必须绑定一个合法 TaskStep

`task_experiments.step_id` 是 `NOT NULL` 且引用 `task_steps(id)`。M1 single-attempt executor 不能只接收
TaskRun id 后直接 append experiment；它必须先解析或创建合法 step。

M1 默认策略：

```text
每个 manual single attempt 创建一个 dedicated TaskStep。
step lifecycle 用现有 TaskRun service / repository seam 表达 attempt execution。
task_experiments.step_id 指向该 dedicated step。
```

如果实现时选择复用 active step，必须在 W1 closeout 中说明 active step 的状态约束和并发限制。不得使用
null、伪 UUID 或未落库 step id 绕过 FK。

### Submission artifact size 必须有精确定义

16,000,000 bytes cap 检查的对象不是整个 `records/<attempt_id>/`，也不是包含 `train_log.txt` 的 evidence
bundle。M1 采用以下口径：

```text
records/<attempt_id>/submission/
  train_gpt.py as the code payload
  compressed model/checkpoint artifact produced by the run
```

`artifact_size_bytes` = `submission/` 下所有 regular files 的 byte size 总和，排除 `README.md`、
`manifest.json`、`submission.json`、`train_log.txt`、`eval_result.json` 等 evidence files。
`submission/train_gpt.py` 是 code payload 的唯一规范副本，计入 cap；records 根目录不另放
`train_gpt.py` 副本。如需追踪源码来源，使用 manifest `artifact.files[]`、`diff_ref.commit_sha`
或 code ref metadata 表达。

若 upstream Parameter Golf 当前要求的 submission 文件名或压缩模型文件名更具体，M1 实现以 upstream
procedure 为准，并把最终文件名写入 manifest 的 `artifact.files[]`。Harness 必须校验这些文件存在；
缺少 capped artifact 文件时 verdict 为 `error` 或 `rejected`，不能用空目录 size 通过 cap。

### Manifest 是 bundle 的机器入口

`manifest.json` 是 Metric Harness 和未来 Renderer 的最小机器入口。M1 manifest 建议字段：

```json
{
  "schema_version": 1,
  "attempt_id": "attempt_0001",
  "task_run_id": "...",
  "parent_experiment_id": null,
  "upstream_commit": "f5c079314c4877fbb0af378c0abade5a8ca33d3a",
  "budget": {
    "tier": "tier2_a6000",
    "max_wallclock_seconds": 480,
    "train_shards": 1,
    "vocab_size": 1024,
    "tokenizer_path": "./data/tokenizers/fineweb_1024_bpe.model",
    "data_path": "./data/datasets/fineweb10B_sp1024/"
  },
  "run": {
    "seed": 42,
    "command": "...",
    "timeout_seconds": 600,
    "execution_path": "host_command",
    "train_seconds": null,
    "eval_seconds": null
  },
  "metrics": {
    "val_bpb": 1.599,
    "metric_source": "final_int8_zlib_roundtrip_exact",
    "artifact_size_bytes": 123456
  },
  "artifact": {
    "required_files": ["README.md", "submission.json", "manifest.json", "train_log.txt", "eval_result.json"],
    "required_dirs": ["submission"],
    "content_ref": "records/attempt_0001",
    "submission_ref": "records/attempt_0001/submission",
    "files": [
      {"path": "submission/train_gpt.py", "bytes": 12345},
      {"path": "submission/model.bin.zlib", "bytes": 12222111}
    ]
  },
  "verdict": {
    "status": "accepted",
    "reasons": ["single_run_valid_evidence"]
  }
}
```

实际字段可按现有 code style 调整，但必须能表达这些语义。`train_log.txt` 可以很大但仍放 workspace records；
Postgres 只存 `content_ref` 和摘要。

### Harness 输出要可测试

Metric Harness 不应只打印人类文本。它至少输出结构化 JSON，供 executor 写入 `task_experiments.metrics` /
`result`：

```text
status: valid | invalid | error
metrics.val_bpb
metrics.artifact_size_bytes
budget_comparable: true | false
required_files_ok: true | false
reasons: [...]
```

Parser 必须 fail closed：

- 找不到 final exact metric line -> `error`；
- metric 非 finite number -> `error`；
- 多个候选 final line -> 使用明确最后一条，或报 duplicate ambiguity，规则需测试固定；
- budget / data path 缺失 -> `invalid`；
- capped submission artifact 缺失或超 cap -> `invalid`。

### Workspace mutation 必须可审计

M1 允许不自动 revert，但必须记录 workspace 状态：

- attempt 前 git status / commit；
- attempt 后 diff summary；
- records ref；
- 如创建 commit，写入 `diff_ref.commit_sha` / `branch` / `parent_commit`；
- 如不创建 commit，写入 `diff_ref.workspace_dirty = true` 和原因。

Git 是 lineage evidence，不是 metric truth。commit 失败不能覆盖已经写入的 harness verdict；应在 result 中增加
`lineage_warning`。

### Budget comparability gate

M1 不为非 reference tier 引入特例分支。Harness 的 budget comparator 必须参数化于 M0 冻结的 reference
budget profile，而不是硬编码某个临时 smoke backend。

`budget_comparable=true` 仅当 candidate 与 reference profile 匹配：

- tier / GPU profile；
- wallclock budget；
- train shard count；
- vocab size；
- tokenizer path；
- data path；
- validation/eval boundary；
- metric source。

任何 non-reference budget result 都不能产生 `accepted` verdict。它可以作为 debug / plumbing evidence 记录，
但必须带 `budget_comparable=false` 和具体 reason。Tier 3 / H100 属于 future milestone；只有重建对应 baseline
后才能进入可比 metric 判断。

## 工作分解

| ID | Phase | 工作项 | 产出 |
| --- | --- | --- | --- |
| W0 | M1a | Contract refresh | 当前 P2 experiment seam、M0 gate、P3 architecture 复核笔记 |
| W1 | M1a | Entrypoint design | CLI / core helper 签名、参数、permission 需求、skip 条件 |
| W2 | M1b | Bundle schema | manifest schema、README / submission 最小模板、submission/ 布局、records dir writer |
| W3 | M1c | Metric parser | final exact val_bpb parser、submission artifact size calculator、budget comparator |
| W4 | M1c | Harness command | `valid/invalid/error` JSON result、reason vocabulary、unit tests |
| W5 | M1d | Single-attempt executor | run command、timeout、log capture、bundle generation、harness invocation |
| W6 | M1d | Ledger integration | append `task_experiments` with P3 payload and compatibility `decision` |
| W7 | M1e | Git lineage capture | diff summary、optional scoped commit、`diff_ref` mirror |
| W8 | M1f | Automated tests | focused tests for parser / harness / bundle / ledger payload |
| W9 | M1f | Manual A6000 smoke | one real Tier 2 attempt findings with command, log refs, verdict |
| W10 | M1g | Closeout | M1 closeout、progress update、M2/M3 deferred list |

### W0. Contract refresh

Requirements:

- Reopen M0 closeout and confirm M1 gate remains `GO`.
- Reopen P3 architecture sections for TaskRun mapping, workspace layout, attempt/commit relation, Metric Harness.
- Inspect current P2 files listed in the header and confirm:
  - `task_experiments` append/list can carry JSONB payloads required by M1;
  - host command / permission seam can run a 480s training command with explicit timeout under shell policy hard cap;
  - current summary reducer limitations are deferred to M3 and do not block single attempt evidence;
  - M1 writes `task_experiments.decision` strictly as `keep` / `revert` / `blocked` and never writes
    `accepted` / `rejected` / `error` into that column;
  - M1 only writes `revert` if it actually performs a governed revert; otherwise rejected/error candidates use `blocked`;
  - event-type mapping covers every compatibility decision M1 can produce.

Verification:

- Implementation notes or closeout list any drift as `confirmed / deferred / blocker`.
- No plan item creates per-attempt TaskRuns or schema migration.

### W1. Entrypoint design

Requirements:

- Choose the thinnest user/operator entrypoint. Preferred shape:

```text
magipi taskrun attempt <task-run-id-or-prefix> \
  --anchor parameter-golf-mini \
  --workspace <parameter-golf-workspace> \
  --hypothesis-file <path> \
  --command <train-command> \
  --seed <seed> \
  --timeout-seconds 600
```

- Use a single-word `taskrun` subcommand to match existing CLI shape (`start`, `status`, `run`, `step`, ...).
  Do not use `experiment-attempt` as the command name.
- Entrypoint must require explicit workspace and command. Do not silently discover `/tmp/neomagi_p3_m0`.
- Permission profile / host command policy must be explicit. Preferred execution path is the existing policy-governed
  host command seam, not the agent bash tool path.
- The entrypoint must create or resolve the dedicated `TaskStep` before appending `task_experiments`.
- Timeout validation depends on execution path:
  - host command path: timeout must be >= 500s and <= shell policy / permission profile hard cap;
  - agent bash tool path, if used only for plumbing smoke: timeout must also respect that tool schema's lower hard cap.

Verification:

- CLI help or internal helper docs make clear this is P3 anchor-specific.
- Missing workspace, missing command, missing TaskRun, missing/invalid step, or timeout outside selected execution path range fail fast.

### W2. Bundle schema and writer

Requirements:

- Implement bundle writer for `records/<attempt_id>/`.
- Write `manifest.json` with schema version and fields listed under design constraints.
- Write or copy:
  - `README.md` with hypothesis, changed knobs, command, metric, verdict, reproduction notes;
  - `submission.json` with enough upstream-compatible metadata for M1;
  - `train_log.txt`;
  - `eval_result.json` with harness output;
  - `submission/` containing the capped submission artifact files, including `submission/train_gpt.py`.
- Define and test `artifact_size_bytes` as the sum of regular files under `submission/`, excluding evidence files.
- Do not also copy `train_gpt.py` into the records root.
- Ensure large data / model files are not accidentally copied into NeoMAGI repo.

Tests:

- Unit test manifest round-trip and required file presence.
- Unit test submission artifact size excludes `train_log.txt` / manifest / README and includes files under `submission/`.
- Unit test records path normalization rejects paths outside workspace records dir.

### W3. Metric parser

Requirements:

- Parse final exact `val_bpb` from Parameter Golf train log using the M0 metric source:
  `final_int8_zlib_roundtrip_exact`.
- Reject non-finite values.
- Make duplicate / missing line behavior explicit and tested.
- Preserve the raw source line in harness details for audit.

Tests:

- Valid log extracts expected `val_bpb`.
- Missing metric returns `error`.
- Non-finite metric returns `error`.
- Multiple metric lines follow documented last-line or ambiguity rule.

### W4. Harness command

Requirements:

- Provide a deterministic harness callable from Python/core and, if useful, CLI.
- Inputs:
  - records dir;
  - manifest path;
  - train log path;
  - expected budget config;
  - baseline stats from M0 reference.
- Outputs structured JSON with `valid / invalid / error`, metrics, comparability, reasons.
- Implement M1 verdict mapping:
  - valid + improved over baseline mean + artifact cap ok -> `accepted` with
    `single_run_valid_evidence` and `not_final_significance_verdict`;
  - valid + not improved -> `rejected` with `not_better_than_baseline_mean`;
  - invalid budget/cap/files -> `rejected` with concrete reason;
  - parser/run failures -> `error`.
- Make clear in the structured result that M1 `accepted` has no minimum 0.005 improvement threshold and may be noise;
  the >= 0.005 + Welch p<0.01 gate remains M5 final evaluation.

Tests:

- Artifact over 16,000,000 bytes is `invalid`.
- Budget mismatch is `invalid`.
- Valid but worse metric is `rejected`.
- Valid and better metric is `accepted` but includes `not_final_significance_verdict`.

### W5. Single-attempt executor

Requirements:

- Capture pre-attempt git/workspace state.
- Create the dedicated TaskStep for this attempt before command execution.
- Run the provided training command through the selected execution path with explicit timeout:
  - host command path: default M1 path, governed by permission profile and shell policy hard cap;
  - bash tool path: allowed only for agent/plumbing smoke and must follow tool schema limits.
- Capture stdout/stderr into `train_log.txt`.
- Generate records bundle.
- Invoke harness.
- Do not rely on agent prose to infer metric or verdict.
- If training times out, write an `error` bundle and ledger record if enough metadata exists.

Tests:

- Fake command success path writes bundle and invokes harness.
- Fake command timeout records `error` and does not mark accepted.
- Fake command nonzero exit records `error` with exit code and log ref.
- Executor creates or receives a persisted step id before repository append.

### W6. Ledger integration

Requirements:

- Append one `task_experiments` record for the attempt.
- Field mapping:
  - `step_id` <- dedicated TaskStep id for this attempt;
  - `hypothesis` <- hypothesis file / agent output;
  - `change` <- changed knobs and diff summary;
  - `command` <- exact train command;
  - `metrics` <- harness metrics;
  - `result.verdict` <- canonical P3 status/reasons;
  - `result.artifact` <- content ref and required files summary;
  - `result.significance` <- `{ "final": false, "reason": "single_run_only" }`;
  - `decision` <- P2 compatibility mapping, limited to `keep` / `revert` / `blocked` for M1 candidate attempts;
  - `diff_ref.records_ref` <- `records/<attempt_id>`;
  - `diff_ref.parent_experiment_id` <- null for root attempt.
- Emit existing summary-grade task events if local conventions already exist; do not invent a broad event taxonomy in M1.

Tests:

- Repository append/list returns the P3 JSON payload intact.
- Attempt append fails fast before repository call if no legal `step_id` is available.
- Summary/history display does not crash on P3 verdict payload.
- Existing P2 experiment tests still pass.

### W7. Git lineage capture

Requirements:

- Record tracked diff summary before and after attempt.
- If enabled, create branch `experiment/<attempt_id>` and commit records manifest/README/log summary/code ref.
- Store commit metadata in `diff_ref`.
- If commit is skipped or fails, record warning without changing metric verdict.

Tests:

- Clean fake repo commit path stores `commit_sha`.
- Dirty/untracked unsafe state records `lineage_warning` and leaves ledger queryable.

### W8. Automated tests

Required focused tests:

```bash
uv run pytest tests/cli/core/test_taskrun_experiments.py
uv run pytest tests/storage/test_taskrun_repository.py
uv run pytest <new-p3-m1-test-file>
```

Also run the smallest existing CLI command tests affected by the entrypoint.

Verification:

- Parser / harness tests do not need GPU.
- Executor tests use fake commands and temporary workspaces.
- `git diff --check` passes.

### W9. Manual A6000 smoke

Requirements:

- Use the M0 Tier 2 budget exactly:

```text
MAX_WALLCLOCK_SECONDS=480
train-shards=1
VOCAB_SIZE=1024
TOKENIZER_PATH=./data/tokenizers/fineweb_1024_bpe.model
DATA_PATH=./data/datasets/fineweb10B_sp1024/
```

- Smoke command must pass budget-defining env values inline in the `--command` string, not only through a wrapper
  script or a prior shell `export`. Canonical shape:

```text
DATA_PATH=./data/datasets/fineweb10B_sp1024/ \
TOKENIZER_PATH=./data/tokenizers/fineweb_1024_bpe.model \
VOCAB_SIZE=1024 \
MAX_WALLCLOCK_SECONDS=480 \
python train_gpt.py ...
```

- Before spending a full A6000 attempt, run the harness/parser against an existing M0-style real log or the first
  short captured log and confirm the budget parser recognizes:
  - `train_loader:dataset:... train_shards:1`;
  - `val_bpb:enabled ... tokenizer_path=...`;
  - `val_loader:shards pattern=.../fineweb_val_*.bin ...`.
- Run one candidate attempt on local/cloud A6000.
- Record command, seed, wallclock, final `val_bpb`, artifact bytes, records ref, task_experiment id.
- If the reference budget resource is unavailable, write skip/block reason. Do not substitute any non-reference tier metric.

Verification:

- `dev_docs/logs/p3_m1_single_attempt_smoke_findings.md` contains enough information to rerun.
- The produced `records/<attempt_id>/manifest.json` and `task_experiments` payload agree.
- Smoke findings include the exact harness `actual_budget` details and any parser drift observed.
- If `VOCAB_SIZE` or `MAX_WALLCLOCK_SECONDS` are not echoed by upstream logs, closeout records that these two
  budget fields are command-declared evidence for M1, not independently runtime-observed evidence.

### W10. Closeout

Requirements:

- Write `dev_docs/logs/p3_m1_single_attempt_closeout.md`.
- Update `dev_docs/progress/progress.md` if that file is currently used for active progress.
- Closeout must state:
  - whether real A6000 smoke completed;
  - attempt id / records ref / task_experiment id;
  - exact verdict and reasons;
  - known deferred items for M2/M3/M5;
  - any code or schema drift discovered.

## 验收标准

1. A single attempt can be run end-to-end under the Mini Parameter Golf M0 budget or records a concrete GPU-unavailable block.
2. The attempt produces `records/<attempt_id>/README.md`, `submission.json`, `manifest.json`, `train_log.txt`,
   `eval_result.json`, and `submission/` with `submission/train_gpt.py` counted in the capped artifact size.
3. Metric Harness extracts `val_bpb`, validates capped submission artifact size, validates budget comparability, and emits structured verdict JSON.
4. One `task_experiments` record stores a legal `step_id`, hypothesis, change, command, metrics, artifact metadata,
   records ref, P3 verdict, and P2-compatible decision.
5. No schema migration, second TaskRun runtime, WebUI write path, Gateway, channel, critic, or autonomous multi-attempt loop is introduced.
6. Automated tests cover parser, harness, bundle writer, executor failure modes, and ledger payload compatibility.
7. Closeout documents the real smoke result or the exact blocker; non-reference tier metrics are not used as comparable evidence.

## 风险

- **Parameter Golf log format drift**：parser 应只依赖 M0 冻结的 final exact metric source，并保留 raw source line。
  W9 前必须用真实 M0/M1 日志 dry-check budget parser，尤其是 tokenizer / validation loader 行；格式漂移应
  fail closed 并在 smoke findings 中记录。
- **Budget env hidden behind wrapper**：M1 harness 从 `--command` 派生 `DATA_PATH`、`TOKENIZER_PATH`、
  `VOCAB_SIZE`、`MAX_WALLCLOCK_SECONDS`。如果 operator 只用 wrapper script 或提前 `export`，harness 会
  fail closed；W9 runbook 必须使用 inline env command。
- **Command-declared budget fields**：若 upstream train log 不回显 `VOCAB_SIZE` / `MAX_WALLCLOCK_SECONDS`，
  M1 只能把这两项作为 command-declared evidence，而不能证明 runtime actually consumed them；closeout 必须
  明确这个限制，后续 milestone 可通过 upstream log echo 或 harness-side instrumentation 强化。
- **Long command timeout conflict**：host command path 可使用 shell policy hard cap；agent bash tool path 可能有更低 tool schema cap。
  M1 必须先固定执行路径，再验证 timeout。
- **Attempt id ordering awkward**：如果 DB id 只能 append 后获得，先 staging 再 atomic rename，避免 records/DB 分裂。
- **Workspace dirty state**：M1 可以 fail closed 或记录 lineage warning；不要为了 commit 破坏 metric ledger。
- **GPU unavailable**：自动化测试仍可完成，但 M1 不能标记真实 anchor smoke done；closeout 必须写 blocked/skip。
- **Scope creep into M2/M3**：artifact read model、summary reducer、parent column、Renderer 均 deferred。

## 提交建议

1. `feat(p3): add parameter golf attempt bundle and harness`
2. `feat(taskrun): record p3 single-attempt evidence in experiment ledger`
3. `test(p3): cover single-attempt harness and ledger payload`
4. `docs(p3): close out m1 single-attempt smoke`
