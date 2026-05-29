---
doc_id: 019e74c0-2e4e-7620-8e05-477a9c18830b
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-29T19:20:12+02:00
---
# 0026-keep-p3-attempts-inside-one-taskrun-session

- Status: accepted
- Date: 2026-05-29
- Related:
  - `design_docs/roadmap/p3_experiment_loop_mvp.md`
  - `design_docs/architecture/p2_taskrun_architecture.md`
  - `design_docs/data_models/task_experiments.md`
  - `design_docs/decisions/0025-use-git-as-p3-attempt-workspace-lineage.md`
- Scope: P3 experiment session grain, attempt grain, and trajectory carrier. This ADR does not change P2 TaskRun truth or P1 compaction semantics.

## 选了什么

- P3 的一个 Experiment Session 映射为一个 TaskRun。
- 该 TaskRun 继续使用一个 long-lived AgentSession。
- Attempt 不拥有自己的 TaskRun，也不拥有自己的 AgentSession。
- Attempt 是同一 TaskRun / AgentSession 内的一次 step slice，并持久化为一条 `task_experiments` record。
- Trajectory truth 来自 Postgres-backed `task_experiments` / `task_steps` / `task_events`，而不是来自 LLM summary。
- P3 直接复用 P2 deterministic TaskRun summary 的三个字段承载 trajectory context：

```text
P2 deterministic field    P3 content
────────────────────────────────────────────────────────────
current_best              { attempt_id, val_bpb, artifact_size }
last_attempt              { attempt_id, hypothesis, verdict_status }
next_action               { hypothesis_seed, branch_to_explore, rationale }
```

- Actor 每完成一个 attempt 后，runtime 从 Postgres truth 重新计算上述字段，并在下一次 step start 时注入 provider-visible TaskRun summary。
- Compaction 只负责在同一个 AgentSession 的 context 压缩后继续暴露 deterministic TaskRun summary；它不是 trajectory truth，也不是 trajectory 更新点。
- LLM 可以提出 hypothesis / rationale，但写入 deterministic trajectory fields 前必须经过 runtime / Metric Harness 校验。

## 为什么

- P2 已经定义 deterministic TaskRun summary fields：`current_best`、`last_attempt`、`next_action`。P3 的 metric trajectory 正好落在这些字段上，不需要新建跨 session trajectory 系统。
- P2 的保护机制依赖同一个 TaskRun / AgentSession：step start 时 host 从 DB 重建 summary 并注入 context；compaction 后同一 session 仍能看到这些结构化字段。
- 如果每个 attempt 都新建 TaskRun / AgentSession，attempts 会被切到不同 session，P2 的同 session rehydration / compaction 机制无法保护跨 attempt trajectory；P3 反而需要自建一套跨 session trajectory persistence 和 injection。
- Keeping attempts inside one TaskRun keeps P3 aligned with P2's existing model: TaskRun owns the long-running task; experiments are child records of TaskRun steps.
- 区分 truth 与 carrier 可以避免把 LLM compaction summary 误当成 metric truth。数值、verdict、stop condition 必须来自 DB-backed records；compaction 只是上下文可见性机制。
- 该决策与 ADR-0025 互补：
  - ADR-0025：Git 表达 attempt 的 workspace diff / lineage truth。
  - ADR-0026：TaskRun / AgentSession 表达 experiment session runtime grain，Postgres deterministic summary 表达 trajectory carrier。

## 放弃了什么

- 方案 A：每个 attempt 一个 TaskRun / AgentSession。
  - 放弃原因：会切断 P2 deterministic summary 在同一 session 内跨 attempt 保留 trajectory 的能力，迫使 P3 自建跨 session trajectory 机制。
- 方案 B：让 compaction summary 自己保存 current best / last attempt / next action。
  - 放弃原因：compaction summary 是 LLM 生成的 session context artifact，不是 truth；P2 架构已明确 TaskRun state 不活在 compaction summary 中。
- 方案 C：新建 P3-only trajectory store。
  - 放弃原因：重复 P2 `task_experiments` + deterministic TaskRun summary 已有能力，增加 truth split 风险。
- 方案 D：让 Git commit / branch 成为 trajectory truth。
  - 放弃原因：Git 适合 workspace diff lineage，不适合结构化 metric / verdict / next action truth；这些仍由 Postgres records 承担。
- 方案 E：只依赖 agent 自述的下一步计划。
  - 放弃原因：会重新引入 prompt memory drift；`next_action` 必须是 host-validated structured payload。

## 影响

- P3 architecture 必须把 Experiment Session、TaskRun、AgentSession 的 grain 绑定在一起：`1 Experiment Session = 1 TaskRun = 1 long-lived AgentSession`。
- P3 implementation plan 不得为每个 attempt 创建新的 TaskRun；attempt 只能创建 / 更新 `task_experiments`、`task_steps`、Git commit 和 records artifact metadata。
- `current_best` 的 P3 reducer 必须按 metric direction、accepted / significant verdict、artifact validity 计算真正 best；不能沿用 "latest keep wins" 的临时逻辑。
- `last_attempt` 必须表达最近一次 attempt 的 structured verdict，而不是仅表达最近 step status。
- `next_action` 必须支持 `{ hypothesis_seed, branch_to_explore, rationale }` 这类结构化 payload，并在写入前经过 runtime / harness 校验。
- Compaction tests for P3 must prove：多次 attempt 后触发 compaction / overflow recovery，下一步 agent context 中仍有精确的 `current_best.val_bpb`、`last_attempt.verdict_status` 和 `next_action` 字段。
- WebUI Renderer 读取 Postgres read model 展示 trajectory，不从 compaction summary 推导 trajectory。
- If a future design needs attempt-level TaskRuns, it must introduce a new ADR that replaces this one and explains how cross-session trajectory truth and context injection are preserved.
