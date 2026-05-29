---
doc_id: 019e7449-dd6c-7d98-a304-110ea1aec681
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-29T17:10:58+02:00
---
# 0025-use-git-as-p3-attempt-workspace-lineage

- Status: accepted
- Date: 2026-05-29
- Related:
  - `design_docs/decisions/0008-memory-truth-closure-postgres-with-workspace-projection.md`
  - `design_docs/decisions/0020-magipi-workspace-and-global-resource-layout.md`
  - `design_docs/roadmap/p3_experiment_loop_mvp.md`
  - `design_docs/data_models/task_experiments.md`
- Scope: P3 experiment attempts only. This ADR does not change P1/P2 TaskRun truth or general workspace cleanup policy.

## 选了什么

- P3 使用 Git 表达 attempt 的 workspace-state / diff lineage truth。
- 一个 P3 experiment session 仍然是一个 actor TaskRun；每个 attempt 对应一条 `task_experiments` record，并对应一个 Git commit。
- Git commit 记录该 attempt 对代码、配置、脚本、tracked records manifest / README / log 摘要等 workspace 可追溯内容的改动。
- Git branch 表达探索分支：当 agent 从某个 attempt 分叉探索另一条路线时，用 branch 表达 workspace lineage，而不是新建第二套 branch runtime。
- `task_experiments` 继续保存实验 metadata truth，并至少能关联：
  - `commit_sha`
  - `branch`
  - `parent_commit`
  - `parent_experiment_id`
  - `records_ref`
  - metric / verdict / significance / accept-reject reason
- Postgres 仍然是 TaskRun、experiment metadata、metric、verdict、event、permission decision 的 truth。
- Git 不保存大二进制 artifact truth。二进制 artifact 走 `.gitignore`，放在 workspace `records/<attempt_id>/` 下；tracked manifest / README / log 摘要记录路径、大小、hash、复现命令和生成环境。
- `records/<attempt_id>/` 必须在本地自包含：即使二进制文件不进 Git，该目录也应包含足够 metadata 让用户知道 artifact 是什么、如何验证、如何重新生成。

## 为什么

- P3 attempt 的核心变化是 workspace diff：训练脚本、配置、quantization / tokenizer / architecture 选择、records manifest。Git 已经是表达这类 diff lineage 的最成熟工具。
- Git commit / branch 可以天然表达 attempt tree，比在 Postgres 中复制完整 diff/tree 更低熵。
- Postgres 更适合保存结构化 truth：metric、verdict、significance、TaskRun event、permission decision、artifact metadata 和查询索引。
- 将 Git 与 Postgres 分工后，P3 可以同时满足两类需求：
  - 人类能用 Git 查看每次 attempt 改了什么；
  - runtime 能用 Postgres 稳定查询 current best / last attempt / next action / verdict。
- 这与 ADR-0008 不冲突：ADR-0008 反对 workspace projection 成为 memory truth；本 ADR 只让 Git 成为 P3 attempt workspace lineage truth，不让 Git 成为 TaskRun / metric / verdict truth。
- 二进制 artifact 不进 Git，可以避免仓库膨胀、clone 成本上升和 accidental large-file churn；hash + manifest 足以让 Postgres / WebUI / human review 关联到本地 artifact。

## 放弃了什么

- 方案 A：Postgres 保存完整 workspace diff / tree，并用 DB parent 指针完全表达 attempt tree。
  - 放弃原因：重复 Git 已经擅长的内容，增加 schema 和迁移负担，也降低人工审查体验。
- 方案 B：Git 成为全部 attempt truth，包括 metric、verdict、event 和 permission decision。
  - 放弃原因：这些是结构化 runtime truth，需要稳定查询、审计和恢复；Git log 不适合作为 TaskRun event store。
- 方案 C：每个 attempt 只写 `task_experiments`，不产生 Git commit。
  - 放弃原因：workspace 改动 provenance 不够强，branch / rollback / diff review 都会退化成 ad hoc 文件比较。
- 方案 D：把压缩模型等二进制 artifact 一并提交进 Git。
  - 放弃原因：会快速膨胀仓库，污染普通代码审查；P3 需要的是可复现和可验证，不是把所有 bytes 都塞进 Git history。
- 方案 E：用 live session branch runtime 表达 P3 attempt tree。
  - 放弃原因：P3 roadmap 已明确不引入 live session 分支运行时；attempt tree 是 workspace / experiment lineage，不是 message/session branch。

## 影响

- P3 implementation plan 必须定义 attempt commit 时机：
  - attempt start 前检查 workspace 是否处于已知 base；
  - attempt 完成后写 `records/<attempt_id>/` manifest / README / log 摘要；
  - Metric Harness 验证后记录 metric / verdict；
  - runtime 生成 attempt commit，并把 commit metadata 写入 `task_experiments`。
- `task_experiments` 的 P3 差量至少要能记录 Git lineage metadata；具体是新增列、JSON payload，还是 read model，由 P3 architecture 决定。
- P3 auto-commit 只允许在 P3 attempt scope 内发生，并受 permission profile / shell policy / git policy 约束；本 ADR 不放宽普通 TaskRun 的 git commit 治理。
- Branch 命名、commit message 格式、dirty workspace 处理、safe revert、failed / rejected attempt 是否也 commit，进入 P3 architecture / implementation plan 明确。
- WebUI Renderer 读取 Postgres read model 展示 attempt tree，并可链接到 commit / records path；WebUI 不直接从 Git 推导 metric 或 verdict truth。
- 如果未来需要把二进制 artifact 移出本地 workspace（例如 object store、Git LFS、artifact registry），需要新 ADR 或本 ADR amendment；默认 P3 MVP 不引入这些外部 artifact store。
