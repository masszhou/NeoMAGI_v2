---
doc_id: 019dc598-7da2-7617-8c61-57a6900afa87
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T19:03:18+02:00
---
# 0011-freeze-pi-mono-baseline-at-97a38bf6

- Status: accepted
- Date: 2026-04-25
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Roadmap: `dev_docs/plans/p1_m0_pi_baseline_and_fixtures.md`
- Architecture: `design_docs/architecture/p1_pi_cli_technical_architecture.md`

## 选了什么

- P1 开发期间，Pi mono 参考基准固定为 `badlogic/pi-mono` 的 `main@97a38bf6`。
- `97a38bf6` 是 behavior matrix、contract fixture、源码路径引用、协议字段核对和兼容性测试的唯一参考基准。
- 开发期间不追加跟随 pi-mono upstream 更新，不滚动更新到更新 commit，也不按单个 upstream commit 做 cherry-pick 式同步。
- 如未来确需升级 Pi mono 参考基准，必须新增 ADR 或修订本 ADR，并先完成基线 diff review；不能在普通实现 PR 中静默更新。
- 本决策不改变 ADR-0009：NeoMAGI 仍采用产品体验等价 + contract-stable，不追求逐行实现兼容。

## 为什么

- P1 的核心目标是建立 NeoMAGI 自身可交付的 Python CLI / agent contract，而不是持续追 pi-mono 主线。
- pi-mono 主线可能高频变化；开发中追加更新会让 fixture、行为矩阵、源码行号、架构文档和测试期望持续漂移。
- 固定基准能让 M0-M10 的验收口径稳定：同一套 Pi-compatible contract 可以驱动 TUI playback、provider adapter、agent runtime、session import/export 和 extension API。
- NeoMAGI 需要保留 Postgres truth、policy、audit、memory 和 Python 类型系统约束；滚动同步 upstream 容易把这些本地设计约束打散。
- 真正有价值的 upstream 变化应通过显式 diff review 被吸收，而不是在日常开发中隐式改变产品边界。

## 放弃了什么

- 方案 A：持续跟随 pi-mono `main`。
  - 放弃原因：会造成验收口径漂移，且让 P1 变成追 upstream 的移植项目。
- 方案 B：按周期刷新基线，例如每周同步一次 upstream。
  - 放弃原因：看似有节奏，但仍会让 fixture、behavior matrix 和源码引用周期性失效，增加 M0-M10 管理成本。
- 方案 C：只 cherry-pick 看起来有价值的 upstream commit。
  - 放弃原因：局部同步缺少整体 diff review，容易引入隐藏 contract 变化和未记录的兼容性偏差。

## 影响

- 所有 Pi mono 源码路径、行号、fixture source、behavior matrix entry 和架构引用都应以 `97a38bf6` 为准。
- 开发期间发现 upstream 新行为时，默认记录为 future review / backlog，不立即纳入 P1 scope。
- 如果 NeoMAGI 需要修复 `97a38bf6` 中的缺陷，可以作为 NeoMAGI-specific 行为实现，但必须在文档或测试中标注偏离原因。
- P1 验收不因 pi-mono 后续变化自动扩大；只有本仓库已接受的 ADR、roadmap 和 architecture 文档定义验收范围。
- 后续升级基准时，必须列出旧基准到新基准的 contract diff、fixture 影响、行为矩阵变化和迁移计划。
