---
doc_id: 019e37d8-d10b-7115-b3b9-798dea8c716e
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-17T23:30:15+02:00
---
# 0023-agent-core-pi-mono-protocol-parity

- Status: accepted
- Date: 2026-05-17
- Scope: 协议面（protocol surface）零 diff，作用于当前 baseline `97a38bf6`。不涉及逐行实现兼容；不承诺自动跟随未来 pi-mono 升级。
- Related:
  - `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
  - `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md`
- Architecture: `design_docs/architecture/p2_taskrun_architecture.md` § D11
- Roadmap: `design_docs/roadmap/p2_taskrun_whitebox_runtime_supplement.md` § R6

## 选了什么

- `packages/magipi/src/agent_core/` 与 pi-mono `97a38bf6` 在**协议面**保持零 diff。
- "协议面" 是一个封闭集合：
  - `AgentEvent` 的事件类型与 subscriber-observable 发射顺序；
  - `before_tool_call` / `after_tool_call` hook 的 context shape 与 result shape；
  - `Message` / `Content` 的 role 与字段；
  - `Agent.subscribe` / `abort` / `wait_for_idle` / `steer` / `follow_up` 的公开方法语义。
- 协议面**之外**的内部实现配置不受本 ADR 约束：例如 `AgentOptions` 中的 `client` / `get_api_key` / `transport` / `on_payload` / `stream_fn` 等 Python runtime plumbing 字段、internal dataclass 命名、async/await 形态、错误信息文案、private helper 重构等，按 NeoMAGI 工程需要演化即可。
- 任何 NeoMAGI-only 的**白盒观察语义增量**（policy 决策可见性、tool 进度归约、compaction 通知、evidence ledger 等）必须落在 TaskRun 派生层：`task_events` 派生事件、`TaskRunAgentSession` 适配器层、或 `cli/tools/wrapper.py` 包装层，**不下沉到 agent_core 协议面**。
- 对**既有 pi-mono 协议事件**的生产时机做正确性修复（例如 D15 让 `tool_execution_update` 真正在执行期间 emit）属于"让现有协议成立"的 bug fix，不属于协议扩展，**允许落在 `agent_core`**。
- 本 ADR 锁定**当前对齐基准**（与 ADR-0011 一致：`97a38bf6`）。未来 pi-mono 升级是否拉取、何时拉取、是否在某个时点偏离 pi-mono 都是**独立决定**，需要单独 ADR 评估；本 ADR **不承诺**"永远跟随 pi-mono 主线"。
- 不要求与 pi-mono 逐行实现兼容（与 ADR-0009 一致）；只要求协议面零 diff。

## 为什么

- ADR-0009 已经声明 NeoMAGI 走"产品等价 + contract-stable"路线；本 ADR 把该原则下沉到协议面细节，明确"contract-stable"在 agent_core 层意味着对 pi-mono 协议**面**的零 diff（不是逐行实现零 diff）。
- ADR-0011 已经冻结 pi-mono baseline SHA 至 `97a38bf6`；本 ADR 补全另一面：即使在已冻结 SHA 内部，NeoMAGI 也不在 agent_core 协议面上做本地增量。
- 协议面零 diff 让 pi-mono 上游协议测试、fixture、行为矩阵、reference 文档可以直接复用，contract review 成本可控。
- 白盒观察性是 TaskRun 层需求（见 P2 supplement R2-R6 与 D10-D15），不是 agent_core 层需求；放在 TaskRun 派生层符合分层原则，agent_core 复杂度可控。
- 区分"修既有协议事件的生产时机 bug"和"扩协议面"很关键：前者是"让现有 contract 真正生效"（如 D15），后者是"创造新 contract"。只有后者会让 agent_core 偏离 pi-mono；前者属于让对齐真正成立。
- P2 amendment D11 评估过三个 option（reorder agent_core / fork 加 pre-event / TaskRun 层用派生事件补足），最终选第三种正是本 ADR 的具体应用；本 ADR 把这个 case-by-case 决策抽成普适规则。
- 选"当前 baseline 对齐"而非"永远跟随"：保留未来灵活性。若 pi-mono 走向某个 NeoMAGI 不接受的方向（例如增加 NeoMAGI 不需要的协议字段、改变行为承诺），本 ADR 允许 NeoMAGI 停在 `97a38bf6` 不跟随，而不需要先撤销一条更广义的"永远对齐"承诺。

## 放弃了什么

- 方案 A：允许 agent_core 重排 pi-mono 事件顺序（例如把 `tool_execution_start` 改到 `before_tool_call` 之后，让 policy 决策在 start 事件前可见）。
  - 放弃原因：破坏 pi-mono 协议面已发出的事件顺序 contract；下游消费者（含 NeoMAGI 自己的代码与 pi-mono 上游 fixture）的事件顺序预期被改变；ADR-0009 product equivalence 隐含承诺被打破。
- 方案 B：允许 agent_core 增加 NeoMAGI-only 新事件类型 / 新 hook / 新 message role（例如 `tool_call_proposed` 这类 pre-execution 事件）以服务本地观察需求。
  - 放弃原因：从此 agent_core 协议面成为 pi-mono 的 superset；本地观察性需求应当在 TaskRun 派生层满足；扩协议面的代价远大于在 TaskRun 层加派生事件。
- 方案 C：允许 NeoMAGI 协议面增量，但要求每次走 amendment 流程。
  - 放弃原因：经验表明流程把关在多人 PR 下容易松动；硬规则"默认禁止协议面增量、需新 ADR 显式推翻本 ADR"对协议契约更稳妥。
- 方案 D：要求 agent_core 与 pi-mono 逐行实现一致（不只是协议面）。
  - 放弃原因：与 ADR-0009 "产品等价、不追求逐行实现兼容" 冲突；Python/TypeScript 间逐行映射本身意义不大。本 ADR 只锁协议面，不锁实现细节。
- 方案 E：承诺永远跟随 pi-mono 主线、自动拉取未来升级。
  - 放弃原因：未来 pi-mono 演进方向 NeoMAGI 不一定接受；自动跟随会把 NeoMAGI 的产品方向绑死在 pi-mono 上。本 ADR 只锁定**当前**对齐基准 `97a38bf6`；未来升级是单独决定（详见 § 影响）。

## 影响

- 所有改动 `packages/magipi/src/agent_core/` 的 PR 必须先回答："本改动是否触及协议面（`AgentEvent` 类型/顺序、hook context/result shape、`Message`/`Content` role、`Agent.*` 公开方法语义）？"
  - 否：不受本 ADR 约束。改内部 dataclass、improve error message、refactor private helper、调整 Python async plumbing、扩 `AgentOptions` runtime config 字段（如 `client` / `get_api_key` / `transport` / `on_payload` / `stream_fn`）等都不受限。
  - 是：必须有新 ADR 显式推翻或修订本 ADR；普通实现 PR 不能静默扩协议面。
- 对**既有 pi-mono 协议事件**的生产时机做正确性修复（例如 D15 修 `tool_execution_update` 实时性）允许落在 `agent_core`；PR 描述中需明确"本修复是修既有事件的生产时机/数据完整性，不引入新协议元素"。
- TaskRun / TUI / Gateway / Channel 等所有上层模块的**新**白盒观察性需求，落点必须是 `task_events` 派生事件、`TaskRunAgentSession` 适配器、或 `cli/tools/wrapper.py` 包装层；不允许下沉到 `agent_core` 协议面。
- code review checklist 应包含：
  - "本 PR 是否动了 `AgentEvent` 类型、subscriber-observable 顺序、hook context/result shape、`Message`/`Content` role？"
  - "若是，有支撑的新 ADR 吗？"
  - "若是 bug fix，PR 描述是否明确说明是生产时机修复、不引入新协议元素？"
- pi-mono baseline SHA 升级（ADR-0011 治理）**不自动适用**本 ADR。每次升级是独立 ADR 决定：
  - 决定跟随升级 → 新 ADR 评估协议面 diff、影响范围、迁移计划，并显式更新本 ADR 中的 baseline 引用。
  - 决定停在 `97a38bf6` 不跟随 → 新 ADR 说明停止跟随的理由。
  - 决定 fork → 新 ADR 推翻本 ADR，提供 fork 治理方案。
- P2 amendment D11 / D13 / D14 / D15 都受本 ADR 约束：
  - D11 显式应用本原则（option C：协议面零 diff，policy 观察靠 `task_tool_policy_*` 派生事件）。
  - D13 在 agent_core 外的 `TaskRunAgentSession` 层操作，不冲突。
  - D14 是 session-runtime 层重构（`CompactionRuntimeMixin`），不冲突。
  - D15 是既有 pi-mono 协议事件 `tool_execution_update` 的生产时机 bug fix，明确属于"让现有协议成立"的修复（不是协议扩展），不冲突。
- 若未来 NeoMAGI 业务必须扩展协议面（例如自定义 multimodal content type、batch turn、新增 thinking budget 协议字段），必须先新增 ADR 推翻或修订本 ADR，并在新 ADR 中提供：协议演进治理方案、与 pi-mono baseline 升级路径的关系、对 ADR-0009 / ADR-0011 的影响评估。
