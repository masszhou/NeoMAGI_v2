---
doc_id: 019e3611-418d-72f9-9988-e8efd9ad7884
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-17T15:12:41+02:00
---
# P2 Roadmap Supplement: TaskRun White-Box Runtime

## 状态

- Status: accepted
- Date: 2026-05-17
- Parent roadmap: `design_docs/roadmap/p2_taskrun.md`
- Related architecture: `design_docs/architecture/p2_taskrun_architecture.md`
- Related reference (informational): `design_docs/references/reference_openclaw_consumption_pattern.md`
- Discussion stage: accepted（amendments D10-D15 同步 accept）。
- Scope: TaskRun 在 P2 具备进程内消费 magipi 白盒 agent runtime 的能力，用来提高任务完成质量。
- Out of scope: P3 Gateway 公开 API、Gateway 调用方式选型、全量 token 级日志持久化、照搬某个外部项目的 channel runtime。

本补充不改变 P2 的大边界：Gateway 仍然不属于 P2，未来 Gateway 可以只负责启动 TaskRun、查询状态和读取最终结果。

这里补的是另一层需求：TaskRun 是 NeoMAGI / magipi 智能化的核心机制之一。它不能只把 agent 当作一个“启动后等待最终回答”的黑盒进程，而必须能在 magipi 进程内消费 agent loop 暴露出来的结构化事实，让任务推进从黑盒总结变成白盒过程。

## 1. 用户需求口径

用户要的不是更多日志本身，而是更可靠的任务完成。

真实任务里，最终回答经常不足以判断任务是否真的完成：

```text
工具可能失败，但最终总结没有准确反映。
测试可能只跑了一半，但 agent 认为已经验证。
权限请求可能被拒绝，但后续计划没有调整。
上下文可能压缩或恢复，但下一步依据不够稳定。
长任务可能中断，但恢复时丢掉了关键执行事实。
```

TaskRun 需要把这些过程事实变成可消费的运行时输入，而不是只在结束后保存一段文本日志。

用户视角下，TaskRun 应该能做到：

```text
知道当前 step 真实进行到哪里。
知道 agent 调用了哪些工具，工具是否成功，关键输出是什么。
知道 permission / policy 决策如何影响了当前 step。
知道当前失败是模型判断、工具失败、权限阻塞、预算耗尽还是用户中断。
知道哪些事实应该进入下一轮任务摘要，哪些只是调试噪音。
能在必要时中断、恢复或重新规划，而不是等最终回答后再补救。
```

这意味着 TaskRun 需要消费 magipi 白盒底座提供的 agent lifecycle、tool lifecycle、policy decision、message / reasoning / summary 等结构化信号。消费这些信号的目的不是展示更热闹的 UI，而是提高任务完成质量。

## 2. 用户价值

### 2.1 更准确的完成判断

TaskRun 应该能基于实际过程判断 step 是否完成，而不是只相信模型最终说“已完成”。

例如，一个修复任务至少需要把这些事实纳入完成判断：

```text
相关文件是否确实修改。
测试命令是否确实运行。
测试结果是否成功或有明确失败原因。
失败是否被记录为 blocker、retry、replan 或 final failure。
```

### 2.2 更稳定的恢复与继续

长任务中断后，用户关心的是“继续工作时别忘掉关键事实”。

TaskRun 需要从白盒事件中提取稳定恢复摘要，例如：

```text
最后完成的 step
当前 blocker
上次工具失败原因
已验证的证据
下一步建议
不应重复执行的动作
```

这些信息应该来自运行时事实，而不是完全依赖 agent 事后自述。

### 2.3 更可控的自动化

自动化任务不能把所有控制权交给 prompt。

TaskRun 需要基于进程内信号识别：

```text
什么时候应该继续。
什么时候应该停止。
什么时候应该 abort 当前 step。
什么时候应该把问题标成 blocked。
什么时候应该把新信息注入下一轮执行。
```

这让 `guarded` / `full` 这类权限模式有真正的运行时支撑，而不是一句“不要问用户”的提示词。

### 2.4 更低熵的用户体验

用户不应该被迫从完整日志里人工还原真相。

TaskRun 应该把白盒过程压缩成少量高价值状态：

```text
正在做什么
已经证明了什么
卡在哪里
下一步是什么
为什么相信当前结果
```

完整事件可以保留给审计和调试，但默认用户体验应该面向任务状态，而不是面向原始日志流。

## 3. P2 补充需求

下面需求分成两层：

- **消费机制层**：进程内消费所需的 primitive（`subscribe` / `before_tool_call` / `after_tool_call` / `abort` / `steer` / `follow_up` / 10-frame `AgentEvent` 与 5-frame session 扩展）。基本已落在 `packages/magipi/src/agent_core/` 与 `cli/core/session_types.py`，本补充**不要求新建**。
- **任务语义层**：把 raw event 翻译为任务质量信号（liveness 终态、证据账本、claim vs. evidence 一致性、resolver 介入点、rehydration 事实）。本补充要求 P2 落地。

R1-R6 给出语义层的硬需求；机制层 primitive 复用现有实现，不在此重述。具体的外部消费模式参照见 `design_docs/references/reference_openclaw_consumption_pattern.md`（informational，不进入 accepted decisions）。

### R1. TaskRun 必须具备进程内白盒消费能力

P2 不能只实现“启动 agent，等待最终结果，写入 TaskRun summary”。

TaskRun core 必须能在 magipi 进程内订阅或接收 agent runtime 的结构化事件，并把这些事件转化为 TaskRun step 的状态、证据、blocker、恢复摘要和审计记录。

### R2. 白盒消费服务于 step 质量，而不是日志堆积

TaskRun 不需要默认持久化每一个 token delta。

P2 更重要的是把关键结构化事件转成任务语义：

```text
step started / completed / failed / blocked / cancelled
tool started / succeeded / failed
policy allowed / denied / blocked
budget exhausted
abort requested
compaction occurred mid-step
auto retry attempted / exhausted
resume context generated
final outcome supported / unsupported by evidence
```

`compaction_*` 与 `auto_retry_*` 在交互式路径已经能产生，但 TaskRun headless runner 当前不消费它们——既有生产侧的缺口，也有消费侧的缺口。技术路径选择（生产侧下沉、taskrun runner 接入 mixin、或定义 headless adapter）由架构 amendment D14 决定。在 D14 落地之前，本条作为期望需求，不阻塞 M1-M6 / 当前 P2 verification；D14 accept 后这两类事件在 headless 路径可见即成为 M7 acceptance 的一部分（见架构 §P2-M7）。

另一个 agent_core 侧的现状缺口：上述事件列表隐含"实时可见"——长工具（bash / pytest / benchmark）在执行期间产生的 partial 进度应该被订阅者即时看到，而不是等工具返回后批量补发。但当前 `agent_core` 的 `tool_execution_update` 是 buffered 模式（`on_update` 回调只 append 到 list，tool 返回后才一次性 emit），与 pi-mono 行为不一致。5 分钟的长工具在执行期间订阅者看不到任何进度信号，违反 R2 "过程事实转化为任务语义"的隐含前提。技术修正由架构 amendment D15 精确化；D15 不阻塞 M1-M6 / 当前 P2 verification，accept 后实时性即成为 M7 acceptance 的一部分（见架构 §P2-M7）。

### R3. TaskRun 需要可中断、可恢复、可订阅的运行时对象

用户需求层面的目标是：TaskRun 不是一次性 subprocess wrapper，而是一个可管理的任务运行。

P2 应该为 TaskRun 保留这些能力：

```text
订阅当前运行过程
取消当前 step 或整个 TaskRun
把中断原因写回任务状态
在恢复时带回关键过程事实
为后续 steer / follow-up 留出边界
```

本条不预设具体 API 名称；技术设计后续在 architecture 文档中落实。

护栏：P2 只要求 TaskRun service 在 magipi 进程内消费这些事件并落到 `task_events` / step state；**是否将 subscription 作为可外部调用的 API 暴露，属于 P3**。P2 的内部事件总线设计应可被 P3 选择性导出，但 P2 不冻结该外部接口形状。

### R4. TaskRun 需要把白盒事实用于结果质量控制

TaskRun 应该能把过程事实用于判断最终结果是否可信。

例如：

```text
声称完成但没有验证证据，应标记为需要验证。
工具失败后仍然给出成功结论，应标记为不一致。
权限被拒后无法继续，应标记为 blocked，而不是伪装完成。
测试失败但有明确下一步，应生成 next action，而不是只返回失败文本。
```

最小可验收反例（架构 amendment D12 必须明确处理）：

```text
TaskRun step 中，模型最终回答声称"已修复并跑了测试"。
但 step 范围内没有任何成功的测试运行证据（无对应工具执行成功记录）。
则 step 不允许收敛到 done；必须落到 blocked 或 failed，并在 step 输出
里明确说明 "缺证据" 或 "声称与证据不一致"。
```

roadmap 层立场（指导 amendment D12）：

- step 生命周期 status 不扩 taxonomy，保持现有值；
- 质量信号（claim vs. evidence）独立成字段，由 D12 命名；
- 一致性校验在 step 收敛侧一次性算出，view 侧不重算。

具体事件名、verification_state 字段编码、与 status 的耦合方式由 D12 精确化。
roadmap 只声明用户层不可接受的反例。

### R5. Gateway 入口不决定 TaskRun 内部质量

Gateway 未来可以继续通过 CLI 启动 TaskRun，并只拿最终结果或摘要。

但 TaskRun 内部不能因此退化成 CLI 黑盒。P2 的关键要求是：TaskRun core 自己要能消费 magipi 进程内白盒底座。Gateway 是否使用 SDK、CLI 或其他 host API，是 P3 的入口形态问题，不应该削弱 P2 TaskRun 的内部运行时能力。

### R6. TaskRun 必须有单一、代码层、tool body 执行前的策略决策点

权限策略不能只藏在 tool wrapper 内部，也不能只靠 `subscribe` 事后观察。

用户层的硬需求：

```text
TaskRun step 中，任何 tool 真正运行前，PermissionProfile 决策必须已经完成、
  已经写入 audit、已经记录在 task_permission_decisions。
denied 的 tool 在 step 视图里有明确的 task event，不依赖 tool 自报。
"决策散落在多处" 与 "wrapper 内决策、事件无法解释" 都不接受。
```

为什么是 P2 而不是 P3：

```text
P2 的 PolicyDecision resolver 已经存在；bounded auto-loop（M5）以及白盒
runtime（M7）的前提都是 "headless 不停下" + "证据可解释"。决策点散落 =
自动模式仍然可能卡死或越权。
```

技术契约由架构 amendment D11 精确化，包括：

- `before_tool_call` 与 `tool_execution_start` 的事件顺序（D11 已收敛到 option C：保 agent_core 与 pi-mono 协议严格对齐，policy 观察性由 D10 派生事件 `task_tool_policy_resolved` / `task_tool_policy_blocked` 通过 `tool_call_id` 关联补足）；
- 当前 wrapper-resident resolver 的迁移路径；
- hook 与 wrapper 的 policy 决策数据传递通道（`PolicyResolutionStore`）；
- denied 路径的 task event 形态。

本条与架构 §D5（PermissionProfile resolver 扩展 PolicyDecision）一致：§D5 描述 resolver 的内部契约，R6 声明用户层不可接受的反例，D11 落实机制。

## 4. P2 完成口径补充

在本补充被接受之前，P2 TaskRun 的完成口径需要增加一条硬边界：

```text
如果 TaskRun 只能启动 agent 并读取最终文本结果，则 P2 TaskRun 不完整。
```

更完整的口径是：

```text
TaskRun 可以从 magipi agent runtime 的结构化过程信号中提取任务状态、证据、失败原因、恢复摘要和控制决策。
```

这条需求的核心不是“用 SDK 替代 CLI”，而是“TaskRun 层必须有 in-process 白盒反馈能力”。CLI 可以继续作为外部入口；白盒能力应该存在于 TaskRun core 内部。

## 5. 后续讨论问题

后续技术架构讨论至少需要回答：

```text
magipi 当前 agent loop 暴露哪些稳定事件？
哪些事件应成为 TaskRun truth，哪些只作为临时过程信号？
TaskRun 如何订阅、取消、恢复一个运行中的 agent step？
TaskRun 是否需要统一 runtime object，还是复用现有 AgentSession runtime？
白盒事件如何进入 summary / next action / blocker，而不制造日志噪音？
Gateway 未来需要读取哪些 TaskRun projection，而不是直接耦合 agent loop？

task_events.payload 是否需要 schema 版本化？一旦白盒事实进入 truth，
  migration 就是长期负担。

step 终态是否要类似 openclaw 的中间 liveness 状态（working / blocked /
  abandoned / error），还是只在 task_steps.status 上扩展新值？

AgentToolResult.details 中 TaskRun-relevant 字段集合归属：tool 自报，
  还是 TaskRun-side contract？当前 _StepEventCollector 已经隐式读
  details.policyDecision / details.toolFinalizeErrors（taskrun_runner.py:236-246），
  这是隐式契约，需要显式归属。

TaskRun service 是否直接持有 Agent 实例（当前 taskrun_runner.py 做法），
  还是经一层 TaskRunAgentSession adapter（更接近 openclaw 的 AgentSession
  抽象，为未来 P3 Gateway 暴露统一对象）？

listener 顺序保证机制：当前 listener 是裸 async def，handler 内部一旦出现
  await 可能导致事件乱序污染 state。需要 asyncio.Queue / sequential await
  chain / per-event lock 中的某一种。
```

这些问题进入 architecture 阶段再定。本文件先固定用户需求：TaskRun 必须把 magipi 白盒底座转化为更高质量的任务推进能力。
