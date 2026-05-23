---
doc_id: 019e5428-5e24-7300-afd6-39bd3ff1a92e
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-23T11:26:24+02:00
status: accepted
date: 2026-05-23
---
# 0024-introduce-webui-operator-surface

- Status: accepted
- Date: 2026-05-23
- Related: `design_docs/decisions/0018-package-neomagi-pi-as-monorepo-product-boundary.md`
- Architecture: `design_docs/architecture/p2_taskrun_architecture.md`
- Roadmap: `design_docs/roadmap/p2_taskrun.md`

## 选了什么

- 引入 `packages/webui` 作为 NeoMAGI 的浏览器操作界面边界。
- WebUI 是 operator-facing surface：用于查看状态、承载人机交互和未来 Gateway 交互界面；它不是新的数据真源，也不是 Gateway runtime 本身。
- 当前第一个真实需求只做 dashboard：登录后只读查看现有 Postgres business schema 中的 Session、TaskRun、Tool、Permission、Usage、Audit 等运行状态。
- 未来 Gateway 落地后，WebUI 可以成为用户与 Gateway 交互的主要界面；具体页面、控件、流程和交互元素按真实需求逐步添加。
- WebUI 不提前设计完整信息架构、任务编排界面、管理后台或 channel UI。暂时没有真实需求的入口可以保留 placeholder。

## 为什么

- NeoMAGI 已有持久 session、TaskRun、tool execution 和 audit 数据；用户需要不用手写 SQL 就能看清当前运行状态。
- Dashboard 是当前最明确、最低风险的 WebUI 切入点：只读、可验证、直接服务调试和日常使用。
- Gateway 是后续交互层，但现在提前冻结 Gateway UI 或 Web 协议会制造空接口和迁移负担。
- 以 `packages/webui` 明确产品边界，可以让浏览器界面独立演进，同时不污染 `magipi` CLI/TUI、TaskRun core 或 Gateway 未来协议。

## 放弃了什么

- 方案 A：继续只依赖 CLI/TUI 查看运行状态。
  - 放弃原因：数据库状态、TaskRun 进度、tool health 和 audit timeline 更适合用浏览器 dashboard 扫描。
- 方案 B：现在就设计完整 Web 平台，包括任务编辑、Gateway channel、用户管理和所有设置页。
  - 放弃原因：缺少真实使用证据，容易做出空页面和过早冻结的交互模型。
- 方案 C：把 WebUI 等同于 Gateway 实现。
  - 放弃原因：WebUI 是用户界面边界；Gateway 是 runtime / host 交互边界。两者可以协作，但不能混成一个模块。
- 方案 D：让 WebUI 自己维护一套运行数据或 projection truth。
  - 放弃原因：违反 Postgres 作为业务真源的既有架构；dashboard 应读取现有 truth，而不是复制 truth。

## 影响

- 第一轮 WebUI 实现应优先完成 authenticated read-only dashboard，而不是 Gateway 交互、TaskRun mutation 或完整后台。
- Dashboard 数据读取以当前 Postgres business schema 为源；WebUI 不引入 SQLite 或独立状态库作为运行真源。
- WebUI 页面和控件按实际用户需求增量加入。没有明确用户路径和验收证据的功能不应提前实现。
- 未来 Gateway 计划可以把 `packages/webui` 作为主要浏览器界面消费者，但 Gateway API、channel identity、WebSocket/event protocol 等仍需在 Gateway 计划或后续 ADR 中单独定义。
- `packages/webui` 可以拥有自己的 auth、static assets、templates/routes 和 dashboard read model；不得直接拥有 TaskRun lifecycle、agent runtime lifecycle 或 storage mutation 语义。
