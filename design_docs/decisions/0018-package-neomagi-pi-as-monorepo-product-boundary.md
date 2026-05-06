---
doc_id: 019dff24-2f00-72f3-bb47-d5769bc085cc
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-06T23:14:14+02:00
---
# 0018-package-neomagi-pi-as-monorepo-product-boundary

- Status: accepted
- Date: 2026-05-06
- Related: `design_docs/decisions/0008-memory-truth-closure-postgres-with-workspace-projection.md`
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Related: `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md`
- Architecture: `design_docs/architecture/p1_pi_cli_technical_architecture.md` § Recommended Python package layout / § P2 Memory Adapter Boundary / § P3 Gateway Boundary
- Roadmap: `design_docs/roadmap/p1_engine_pi.md`

## 选了什么

- NeoMAGI 后续采用 repo-root `packages/` 表达产品边界：`packages/neomagi_pi/`、`packages/memory/`、`packages/gateway/`。
- 当前优先级是让 `neomagi_pi` 作为日常可用的 Pi-compatible local agent shell 继续迭代；本 ADR 不要求现在预先设计 gateway / memory host APIs。
- `neomagi_pi` 需要成为可本地安装的独立包，并预留独立命令入口 `magipi`。后续可以用 `magipi` 承载轻量安装体验，例如类似 `pi install git:github.com/badlogic/pi-telegram` 的 skill / package 安装入口。
- `neomagi_pi` 的第一步迁移可以以保持行为为目标，把当前 `src/` 下的 P1 实现整体搬入 `packages/neomagi_pi/`；不要求同时完成 gateway / memory 共享 substrate 抽象。
- `memory` 和 `gateway` 的接口应从 `neomagi_pi` 的真实使用中抽取。三者最终保持接口一致、实现解耦，但共享边界只在有使用证据后沉淀为 public protocols、schemas、fixtures、conformance tests 和 adapter seams。
- 未来进入 gateway 集成时，`gateway` 可以调用 `neomagi_pi` 和 `memory`，但只能通过 allowlisted host APIs。`gateway` 不拥有它们的内部 lifecycle，不读写它们的内部 storage，不 import TUI / CLI / resource / private modules。
- 本 ADR 授权包命名和演进方向；迁移应优先保持现有入口级行为，再逐步抽取稳定接口。

## 为什么

- `neomagi_pi` 是近期日常使用闭环；memory 设计需要从真实 session、工具使用、摘要、失败和人工回顾中积累素材。
- 独立包和 `magipi` 命令给 Pi-compatible agent shell 一个清晰的本地安装面，避免长期依赖 repo-root 开发入口。
- 轻量 skill / package 安装会成为日常扩展路径；它应该跟随 `neomagi_pi` 的真实使用逐步长出来，而不是提前实现完整包管理器。
- Gateway 设计仍然靠后。过早要求 host API catalog 会把远期集成问题前置，阻挡当前产品使用和经验积累。
- 接口一致、实现解耦仍是目标，但接口应从稳定用法中提炼，而不是在缺少日常使用经验时提前冻结。

## 放弃了什么

- 方案 A：把独立包命名为 `packages/pythonpi/`。
  - 放弃原因：名称语义不清，容易被误读为 Python runtime 或 Raspberry Pi 相关实现。
- 方案 B：迁移 `neomagi_pi` 时同步完成 gateway / memory 共享 substrate 抽象。
  - 放弃原因：缺少使用证据，会把日常使用闭环变成架构重排前置条件。
- 方案 C：现在就完整预留 gateway / memory host APIs。
  - 放弃原因：缺少使用证据，容易制造空接口和迁移负担。
- 方案 D：让未来 `gateway` 直接 import `neomagi_pi` / `memory` 的内部实现。
  - 放弃原因：会让 gateway 拥有不该拥有的 lifecycle、storage 和 resource 细节。

## 影响

- `neomagi_pi` 的包迁移和日常使用不被 gateway / memory API 预留阻塞。
- 第一轮迁移可以是低抽象、行为保持型迁移：更新 package layout、build config、测试 import 和 `magipi` 入口，不把 gateway / memory host API 作为验收前置项。
- `magipi` 是 `neomagi_pi` 的目标 console command；迁移期间当前开发入口 `uv run python -m cli ...` 继续有效。
- `magipi install ...` 可以作为后续增量能力规划，但初期只要求支持简单、可审计的 skill / package 安装路径，不要求完整复刻 Pi package manager。
- 未来 memory 计划可以先从 `neomagi_pi` 的使用日志、session truth、tool results 和人工回顾中提炼需求，再定义正式 memory API。
- 未来 gateway 计划启动时，再定义 allowlisted host APIs、adapter registration 和 import-boundary tests。
- `memory` 仍以 Postgres ledger 为长期 memory truth；任何长期 memory 写入都必须走 DB-backed memory tool 和显式审批路径。
