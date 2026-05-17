---
doc_id: 019e3632-7138-763d-a2fd-30e03e0781c2
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-17T15:48:42+02:00
---
# Reference: OpenClaw In-Process Consumption of pi-agent

## 状态

- Status: reference (NOT a decision; NOT an ADR)
- Date: 2026-05-17
- Purpose: M0 架构讨论外部参照，服务于 `design_docs/roadmap/p2_taskrun_whitebox_runtime_supplement.md`。
- Source: 外部 repo `pi-mono` (pinned commit [`97a38bf6`][pm-commit]) 与 `openclaw` (pinned commit [`eb3de950`][oc-commit])，观察时点 2026-05-17。所有外部源码引用均 pin 至上述 commit；NeoMAGI_v2 仓库内部路径使用相对路径（per `design_docs/INDEX.md`）。
- Disposition: 本文记录的模式 MAY inform P2 TaskRun 设计，**不绑定** NeoMAGI_v2 实现。选择、拒绝、改造均由 M0 架构讨论决定。本文不进入 `design_docs/decisions/`，不进入 accepted decisions。

## 为什么有这份文档

NeoMAGI_v2 已经把 pi-agent 的核心 runtime 移植为 `packages/magipi/src/agent_core/`。
TaskRun 也已经在 `packages/magipi/src/cli/core/taskrun_runner.py:107` 通过 `agent.subscribe(...)` 订阅了 raw `AgentEvent`。
但 P2 TaskRun 当前只把事件流当作 session jsonl 写入和粗粒度 outcome 聚合的素材，并没有把事件
转化成任务质量信号（参见 `_StepEventCollector` 实现：`packages/magipi/src/cli/core/taskrun_runner.py:201-294`）。

OpenClaw 在 `src/agents/pi-embedded-*` 系列文件里实现了一套相对成熟的 in-process 白盒消费模式。
本文记录其中**七个值得 P2 M0 讨论时参考**的模式，以及**不建议照搬**的部分。

本文不重复 supplement 已经说明的需求口径；只回答"如果要消费 pi-agent in-process 事件，外部
项目是怎么做的"这一具体技术问题。

## 1. pi-agent 生产者侧暴露面（校准起点）

`@mariozechner/pi-agent-core` 暴露以下 in-process 可消费表面（NeoMAGI_v2 已忠实移植，对照表见 §4）：

```text
Agent.subscribe(listener)           订阅 10-frame AgentEvent
Agent.abort() / wait_for_idle()     中断与终态等待
Agent.steer() / follow_up()         运行中注入和运行后追加
Agent.before_tool_call (hook)       唯一可阻止 tool 执行的入口
Agent.after_tool_call (hook)        tool 结果落地前的覆盖入口
Agent.transform_context (hook)      LLM 调用前的 context 变换
Agent.convert_to_llm (callback)     custom message → LLM message
Agent.get_steering_messages         运行中拉取 steering（用于 host-driven 注入）
Agent.get_follow_up_messages        运行结束前拉取 follow-up
```

10-frame `AgentEvent`：`agent_start / agent_end / turn_start / turn_end /
message_start / message_update / message_end / tool_execution_start /
tool_execution_update / tool_execution_end`。

NeoMAGI_v2 在 `packages/magipi/src/cli/core/session_types.py:285-336` 又叠加 5 个 session-level frame：
`queue_update / compaction_start / compaction_end / auto_retry_start / auto_retry_end`。

这些已经是消费基础，本文不重复列出实现细节。

## 2. 七个值得参考的模式

### 2.1 双层事件总线：raw AgentEvent → 派生 stream taxonomy

**位置**：openclaw [`src/infra/agent-events.ts`][oc-agent-events] L5-17, L209-235, L302

OpenClaw 不把 pi-agent 的 `AgentEvent` 直接当作业务事件流。它在 handler 层把 raw event
**翻译**成自己定义的 stream taxonomy：

```text
lifecycle       任务/agent 级生命周期
tool            tool 启动/更新/结束的业务摘要
assistant       assistant 文本块（拆分后）
error           失败原因
item            任务级 item 进度（带 phase / status / approval_id 等）
plan            agent 自报的计划块
approval        权限审批请求与解析
command_output  shell 输出 delta/end
patch           文件 patch summary（adds/modifies/deletes）
compaction      压缩起止
thinking        thinking 块（reasoning level/进度）
```

**对 P2 TaskRun 的启示**：raw `AgentEvent` 是 SDK 协议，TaskRun 业务侧不应在
`task_events.payload` 直接存原始 frame；应该有一层 TaskRun-owned stream taxonomy
（例如 `lifecycle / tool / policy / compaction / evidence / outcome`），保证 `task_events`
表的 schema 长期稳定。

### 2.2 runId-keyed 全局 emit / subscribe（含 TTL 清扫）

**位置**：openclaw [`src/infra/agent-events.ts`][oc-agent-events] L139-235, L186-202 (`sweepStaleRunContexts`)

OpenClaw 用一个 process-global 单例事件总线，按 `runId` 分桶 seq、按 `runId` 注册
`AgentRunContext`，并提供 `sweepStaleRunContexts` 兜底清扫漏掉 terminal 事件的桶。

**对 P2 TaskRun 的启示**：

- TaskRun service 不需要 process-global 单例（magipi 进程内 TaskRun 数量有限），但**单 step
  的事件总线必须有显式 lifecycle**，对应 `task_runs.heartbeat_at` 已经具备的兜底语义。
- 是否要把 TaskRun 的事件流暴露给同进程内的其它消费者（例如 TUI status view）由 M0 决定；
  即使决定暴露，也应在 P2 内闭环，**不要冻结对外 API**（参见 supplement R3 末尾护栏与 R5）。

### 2.3 按事件家族分文件的 handler 层

**位置**：openclaw [`src/agents/pi-embedded-subscribe.handlers.ts`][oc-handlers] L1-16 (dispatch root)

四类 handler 各自占用一个文件：

```text
pi-embedded-subscribe.handlers.lifecycle.ts    agent_start / agent_end / compaction_start / compaction_end
pi-embedded-subscribe.handlers.messages.ts     message_start / message_update / message_end
pi-embedded-subscribe.handlers.tools.ts        tool_execution_*
pi-embedded-subscribe.handlers.compaction.ts   (被 lifecycle 转发)
```

**对 P2 TaskRun 的启示**：当前 `_StepEventCollector` 把所有事件家族塞进同一个类已经吃力
（见 `packages/magipi/src/cli/core/taskrun_runner.py:201-294`）。语义层若引入证据账本与一致性校验，建议按家族拆 handler，
而不是继续在 `_StepEventCollector` 加分支。

### 2.4 多信号收敛的 liveness 终态机

**位置**：openclaw [`src/agents/pi-embedded-subscribe.handlers.lifecycle.ts`][oc-handlers-lifecycle] L40-69

`handleAgentEnd` 不会直接把 `agent_end` 当作 step 完成。它合成多个信号决定终态：

```text
isError                       last assistant.stopReason === "error"
hasAssistantVisibleText       是否有任何对用户可见的 assistant 文本
hadDeterministicSideEffect    是否有已提交的 messaging-tool delivery / cron add 等确定性副作用
incompleteTerminalAssistant   最后一条 assistant 是否以 tool-call stop reason 截断
replayInvalid                 replay 是否破坏
livenessState (派生)          working → blocked / abandoned / error / end
```

例如：assistant 最终以 tool-call 截断（说要调 tool 但没真正完成），就算前面有可见文本，
`livenessState` 也被降为 `abandoned`，避免把"中断的工具链"伪装成成功结束。

**对 P2 TaskRun 的启示**：这是 supplement R4（白盒事实用于结果质量）最直接的实操答案。
现有 `_StepEventCollector.outcome()` 是单层规则（has error → failed；has block → blocked；
has tool_error → failed；否则 done），缺少 incomplete / abandoned 中间态。M0 应决定是
扩展 step status taxonomy，还是用 `task_steps.output.evidence_summary` 子字段承载这些
派生信号。

### 2.5 beforeToolCall 作为唯一可阻止执行的策略入口

**位置**：openclaw [`src/agents/pi-tools.before-tool-call.ts`][oc-before-tool-call]（同目录还有 `.runtime.ts` / `.state.ts` / `.embedded-mode.test.ts` 等 sibling 文件）；以及 [`src/agents/pi-tools.policy.ts`][oc-policy]

OpenClaw 把所有 policy / approval 决策落点在 `Agent.beforeToolCall`，原因：

```text
subscribe 在 tool_execution_start 拿到事件时，tool 已经在执行
让 policy 决定散落在各 tool wrapper 里会破坏单一决策点
audit truth 必须在 tool 真正执行前可见
```

**对 P2 TaskRun 的启示**：supplement 已有 R6（TaskRun 必须用 `before_tool_call` 接入
PermissionProfile resolver）。本模式提供具体落地形状：

```text
raw      = policy.evaluate(request)
resolved = profile.resolve(raw)
audit.write(raw, resolved)
返回 {block, reason} 或 None
```

完整发生在 hook 内部，hook 返回 `{block: true}` 让 agent 在 tool 真执行前发出 error tool result。

### 2.6 派生事件回灌为下一轮 prompt 的 untrusted 块

**位置**：openclaw [`src/agents/internal-events.ts`][oc-internal-events] L43-98, L100-138

`task_completion` 这类内部事件被格式化成结构化 prompt block，作为下一轮 LLM 输入：

```text
<<<BEGIN_UNTRUSTED_CHILD_RESULT>>>
result body
<<<END_UNTRUSTED_CHILD_RESULT>>>
```

明确标注 untrusted、不允许混入 system prompt 域。

**对 P2 TaskRun 的启示**：TaskRun rehydration summary 已经有"deterministic summary from DB"
的契约（架构 §D4）。本模式给出补充：summary 之外，若 step 之间需要带 untrusted 事实
（例如上一步 tool 输出），prompt 注入必须显式标记 untrusted 域、并与 system / policy 域隔离。

### 2.7 串行 handler chain 防止状态突变竞态

**位置**：openclaw [`src/agents/pi-embedded-subscribe.handlers.ts`][oc-handlers] L23-73 (`pendingEventChain`)

OpenClaw 在 dispatcher 内部维护一条 promise chain，让每个事件的 handler 在前一个 handler
（哪怕是 async）完结后才开始执行，防止 `message_update` 顺序错乱、`tool_execution_end`
覆盖 `tool_execution_start` 等状态污染。

**对 P2 TaskRun 的启示**：当前 listener (`packages/magipi/src/cli/core/taskrun_runner.py:92`) 是裸 `async def`，
Python `asyncio` 下不会自动保证顺序——如果未来 handler 内部出现 `await`，乱序会破坏
state machine。M0 设计 listener 框架时需要明确给出顺序保证机制（`asyncio.Queue` / sequential
await chain / per-event lock）。

## 3. 不建议照搬的部分

- **process-global 事件单例**：OpenClaw 这一层是 channel runtime 的产物，TaskRun 不需要。
  magipi 是 CLI / TUI 进程，TaskRun service 持有自己的事件总线即可。
- **`stream: "thinking"` 全量持久化**：OpenClaw 把 reasoning level 与 thinking 输出当作常驻
  stream；这与 supplement §3 R2"不为日志而日志"明确冲突。TaskRun 不应默认持久化 reasoning
  delta。
- **`isControlUiVisible` / 隐藏运行**：openclaw 区分 control UI 是否可见、是否抑制 assistant
  stream，这是它给远程通道 UI 的优化。TaskRun 不需要这一层。
- **`hasCommittedMessagingToolDeliveryEvidence` 等 channel-specific 副作用判定**：OpenClaw
  这套判定是为 IM 通道发送/确认机制设计的。TaskRun 的"确定性副作用"判定应来自工程语义
  （文件修改 / 测试运行 / git 操作），不应照搬。
- **`AgentEventStream = ... | (string & {})` 开放式类型**：OpenClaw 允许任意字符串扩展
  stream，schema 弱契约。TaskRun 的 `task_events.event_type` 落入 Postgres truth，需要更强
  约束（参见 supplement §5 schema 版本化问题）。
- **handler 文件巨型化**：openclaw 单个 handler 文件最大已到 1198 行
  ([`src/agents/pi-embedded-subscribe.handlers.tools.ts`][oc-handlers-tools])。结构值得参考，规模不值得复制。

## 4. 与 NeoMAGI_v2 现状对照

| 能力 | NeoMAGI_v2 当前状态 | openclaw 等价 |
|---|---|---|
| `Agent.subscribe` | `packages/magipi/src/agent_core/agent.py:153` | `Agent.subscribe` (pi-mono [`packages/agent/src/agent.ts`][pm-agent-ts]) |
| `before_tool_call` / `after_tool_call` | `packages/magipi/src/agent_core/agent.py:106-107`（**未被 TaskRun 使用**） | [`src/agents/pi-tools.before-tool-call.ts`][oc-before-tool-call] |
| 10-frame `AgentEvent` | `packages/magipi/src/agent_core/types.py:204-215` | [`packages/agent/src/types.ts`][pm-types]（同源） |
| session-level 扩展 frame | `packages/magipi/src/cli/core/session_types.py:320-336`（已含 compaction / auto_retry / queue_update） | 在 handler 层派生，未作为 frame |
| TaskRun 订阅入口 | `packages/magipi/src/cli/core/taskrun_runner.py:92-107` | `subscribeEmbeddedPiSession` ([`src/agents/pi-embedded-subscribe.ts`][oc-subscribe]) |
| Step outcome 聚合 | `packages/magipi/src/cli/core/taskrun_runner.py:201-294` 单一类，规则浅 | 分文件 handler + liveness 状态机 |
| 派生 stream taxonomy | 无（直接写 raw event 进 session jsonl） | `lifecycle / tool / assistant / error / item / plan / approval / command_output / patch / compaction / thinking` |
| Policy hook 落地 | tool wrapper 内 `PolicyDecision` | `agent.beforeToolCall` 钩子 |
| Run lifecycle 兜底 | `task_runs.heartbeat_at` | `sweepStaleRunContexts` TTL |
| 派生事件注入下轮 prompt | 走 `taskrun summary` deterministic block（架构 §D4） | `formatAgentInternalEventsForPrompt` untrusted block |
| handler 顺序保证 | 无显式机制 | `pendingEventChain` promise chain |

## 5. 引用文件清单

### pi-mono — pinned commit [`97a38bf6`][pm-commit]

- [`packages/agent/src/agent.ts`][pm-agent-ts]
- [`packages/agent/src/agent-loop.ts`][pm-agent-loop]
- [`packages/agent/src/types.ts`][pm-types]
- [`packages/agent/README.md`][pm-readme]

### openclaw — pinned commit [`eb3de950`][oc-commit]

- [`src/infra/agent-events.ts`][oc-agent-events]
- [`src/agents/pi-embedded-runner.ts`][oc-runner]
- [`src/agents/pi-embedded-subscribe.ts`][oc-subscribe]
- [`src/agents/pi-embedded-subscribe.handlers.ts`][oc-handlers]
- [`src/agents/pi-embedded-subscribe.handlers.lifecycle.ts`][oc-handlers-lifecycle]
- [`src/agents/pi-embedded-subscribe.handlers.messages.ts`][oc-handlers-messages]
- [`src/agents/pi-embedded-subscribe.handlers.tools.ts`][oc-handlers-tools]
- [`src/agents/pi-embedded-subscribe.handlers.compaction.ts`][oc-handlers-compaction]
- [`src/agents/pi-embedded-subscribe.types.ts`][oc-subscribe-types]
- [`src/agents/internal-events.ts`][oc-internal-events]
- [`src/agents/internal-event-contract.ts`][oc-internal-event-contract]
- [`src/agents/pi-tools.before-tool-call.ts`][oc-before-tool-call]
- [`src/agents/pi-tools.policy.ts`][oc-policy]

### NeoMAGI_v2 现有对照（仓库内部，相对路径）

- `packages/magipi/src/agent_core/agent.py`
- `packages/magipi/src/agent_core/types.py`
- `packages/magipi/src/agent_core/runtime_types.py`
- `packages/magipi/src/cli/core/session_types.py`
- `packages/magipi/src/cli/core/taskrun_runner.py`
- `packages/magipi/src/cli/interactive/runtime_events.py`
- `packages/magipi/src/storage/taskrun_repository.py`
- `design_docs/architecture/p2_taskrun_architecture.md`
- `design_docs/roadmap/p2_taskrun.md`
- `design_docs/roadmap/p2_taskrun_whitebox_runtime_supplement.md`

[pm-commit]: https://github.com/badlogic/pi-mono/commit/97a38bf65217d89619b3386c620333a97ee391b7
[pm-agent-ts]: https://github.com/badlogic/pi-mono/blob/97a38bf65217d89619b3386c620333a97ee391b7/packages/agent/src/agent.ts
[pm-agent-loop]: https://github.com/badlogic/pi-mono/blob/97a38bf65217d89619b3386c620333a97ee391b7/packages/agent/src/agent-loop.ts
[pm-types]: https://github.com/badlogic/pi-mono/blob/97a38bf65217d89619b3386c620333a97ee391b7/packages/agent/src/types.ts
[pm-readme]: https://github.com/badlogic/pi-mono/blob/97a38bf65217d89619b3386c620333a97ee391b7/packages/agent/README.md

[oc-commit]: https://github.com/openclaw/openclaw/commit/eb3de950251439d6fa3cc8941ab534e90005bdcd
[oc-agent-events]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/infra/agent-events.ts
[oc-runner]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-embedded-runner.ts
[oc-subscribe]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-embedded-subscribe.ts
[oc-handlers]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-embedded-subscribe.handlers.ts
[oc-handlers-lifecycle]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-embedded-subscribe.handlers.lifecycle.ts
[oc-handlers-messages]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-embedded-subscribe.handlers.messages.ts
[oc-handlers-tools]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-embedded-subscribe.handlers.tools.ts
[oc-handlers-compaction]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-embedded-subscribe.handlers.compaction.ts
[oc-subscribe-types]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-embedded-subscribe.types.ts
[oc-internal-events]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/internal-events.ts
[oc-internal-event-contract]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/internal-event-contract.ts
[oc-before-tool-call]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-tools.before-tool-call.ts
[oc-policy]: https://github.com/openclaw/openclaw/blob/eb3de950251439d6fa3cc8941ab534e90005bdcd/src/agents/pi-tools.policy.ts
