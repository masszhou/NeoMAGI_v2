---
doc_id: 019e1e32-9e0f-761b-aa1a-41756ed2b2bf
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-12T23:57:19+02:00
---
# P2 Roadmap: TaskRun

## 状态

- Status: accepted
- Date: 2026-05-13
- Roadmap: `design_docs/roadmap/p2_taskrun.md`
- Architecture: `design_docs/architecture/p2_taskrun_architecture.md`
- Scope: TaskRun core, TaskRun-owned step queue, PermissionProfile / PolicyDecision resolver, bounded auto loop, experiment / benchmark kernel, and P3 Gateway readiness seam.
- Out of scope: Gateway channel/API implementation, full long-term memory system, and final QMD autoresearch showcase README/report packaging.

P2 的核心目标是让 NeoMAGI / magipi 从“一次性对话式 agent”升级为“可持续推进任务的 agent runtime”。

P2 不实现 Gateway。Gateway 计划放到 P3。P2 只在 TaskRun core 中保留 CLI 已实际使用的内部 service seam，例如启动、恢复、取消、查询状态、读取事件流等；P3 再从真实用法中抽取公开 host API contract。

P2 的重点是解决两个问题：

```text
1. 复杂任务如何跨多轮、跨 session、跨上下文窗口持续推进？
2. 自动化任务如何避免因为等待用户确认而卡死？
```

TaskRun 不是长期记忆系统，也不是简单任务列表。它是一层 workspace-scoped 的执行 runtime，用来让 agent 稳定、可恢复、可审计地推进真实工程任务。

## 1. P2 要解决的问题

当前 agent 很擅长单轮任务，但真实工程任务经常不是一次对话能完成的。

例如：

```text
修复一个复杂 bug
分析一个大型 repo
持续优化 benchmark
执行多步骤研究任务
长期跟踪一组 open issues
在多轮实验中比较 metric 并选择最佳方案
```

这些任务都有共同特征：

```text
任务会跨多轮执行
过程需要记录
中间状态不能丢
失败后需要恢复
结果需要可验证
下一步行动需要明确
自动化流程不能因为等待用户确认而停死
```

P2 的 TaskRun 目标是把这类任务从“聊天上下文里的临时计划”变成“workspace 里的持久任务运行”。

## 2. 用户会获得什么

P2 同时保留两类入口：

```text
/taskrun ...
  interactive TUI 内的 slash command 入口。

magipi taskrun ...
  CLI / subprocess / future Gateway 调用的稳定入口。
  P2 hard acceptance 优先以这个入口验收。
```

用户可以在一个 workspace 中启动一个长期任务：

```text
/taskrun start "优化 retrieval pipeline 的延迟，并确保 benchmark 不回退"
magipi taskrun start "优化 retrieval pipeline 的延迟，并确保 benchmark 不回退"
```

之后 magipi 可以持续推进：

```text
记录当前目标
拆分任务步骤
执行一轮实验
运行测试或 benchmark
记录结果
判断是否保留改动
生成下一步建议
在中断后恢复上下文
```

用户可以随时查看：

```text
/taskrun status
/taskrun summary
/taskrun history
/taskrun next
```

更重要的是，用户可以明确选择 TaskRun 的自动化授权模式：

```text
/taskrun start "修复测试失败" --permission guarded
/taskrun start "持续优化 benchmark" --permission full
```

当用户选择 full permission 时，TaskRun 不应该在中途反复问：

```text
是否允许我修改文件？
是否允许我运行测试？
是否允许我继续下一步？
是否允许我保留这个改动？
```

这些确认应该由 runtime 的 permission policy 自动处理，而不是交给 LLM 临场判断。

## 3. P2 的核心能力

### 3.1 持久任务状态

每个 TaskRun 都会在 workspace 内拥有独立状态。

状态至少包括：

```text
任务目标
当前阶段
当前步骤
已完成步骤
失败步骤
下一步候选
运行预算
停止条件
最近一次结果
最终结论
```

这些状态不会只依赖 LLM 上下文，而是持久保存在 Postgres 中。workspace 下的 `.magipi/taskruns/` 只作为 human-readable projection / export，不是 TaskRun truth。

这意味着即使对话中断、上下文压缩、agent 重启，TaskRun 仍然可以恢复。

### 3.2 可审计事件日志

TaskRun 会记录关键事件，而不是只保留最终回答。

例如：

```text
用户输入
agent 决策
工具调用
命令输出
文件修改
测试结果
benchmark metric
keep / revert 决策
错误与恢复动作
permission approval / denial
```

用户可以回看 agent 到底做了什么，而不是只能相信一个最终总结。

TaskRun event truth 写入 Postgres。`events.jsonl` 可以从 DB 生成，用于人工查看和导出，但手工编辑不会自动变成真源。

### 3.3 Step-based 执行模型

P2 不追求一开始就让 agent 无限自动运行。

第一版 TaskRun 采用 step-based 模型：

```text
一次 step = 一次明确的推进
```

每个 step 都应该有输入、动作、输出和结论。

例如：

```text
Step 1: 阅读 benchmark 配置
Step 2: 运行 baseline
Step 3: 修改 cache 策略
Step 4: 重新运行 benchmark
Step 5: 比较结果并决定保留或回滚
```

这样可以避免 agent 在没有边界的情况下长时间乱跑。

第一版采用：

```text
1 TaskRun = 1 long-lived AgentSession
1 step = 该 AgentSession 内的语义切片
```

也就是说，TaskRun.step 复用 P1 的 `AgentSession.prompt()`、durable session、resume/tree、compaction、branch summary 和 provider cache affinity；P2 不为每个 step 新建 session。

### 3.4 Non-interactive PolicyDecision Resolver

这是 P2 必须补齐的核心能力，但它不是第二套 policy 类型系统。

大模型在交互式对话里经常会停下来征求用户意见。这在普通聊天中是合理的，但在 TaskRun 自动化流程中会变成不可控终止点。

例如模型可能说：

```text
我可以继续修改这个文件吗？
是否需要我运行测试？
我是否应该删除这个临时文件？
这个改动是否要保留？
```

在自动化 TaskRun 中，这类问题不能直接变成“等待用户输入”。

P2 在现有 `PolicyRequest` / `PolicyDecision` / governed tool wrapper 之上增加 `PermissionProfile` 和 non-interactive resolver，由代码层统一处理所有授权请求。

核心原则：

```text
是否允许执行，不由 LLM 临时决定
是否需要询问用户，由 permission policy 决定
在 non-interactive 模式下，TaskRun 不能无限等待用户输入
所有 approval / denial 都必须写入事件日志
```

PermissionProfile resolver 至少支持三种模式：

```text
interactive
  默认交互模式。
  高风险操作可以询问用户。

guarded
  自动批准安全范围内的操作。
  超出策略的操作直接拒绝或标记为 blocker，不等待用户。

full
  用户显式授权后，在配置范围内自动批准操作。
  TaskRun 不应该因为确认请求而停住。
```

`full permission` 不应该只是一句 prompt，例如“不要问用户，直接执行”。这不可靠。

它应该是代码层机制：

```text
tool produces PolicyDecision
  -> PermissionProfile resolver checks scope
  -> auto allow / deny / fail
  -> write audit + task event
  -> continue execution or fail step
```

也就是说，工具层不能直接问用户。工具层只能返回 `PolicyDecision(confirm)`，由 runtime 按当前 profile 解析；headless / non-interactive 模式不能进入 TUI confirm path。

### 3.5 Full Permission Scope

`full permission` 也不应该等于完全无限制的宿主机权限。

更合理的定义是：

```text
在用户预先声明的 scope 内，全自动批准。
超出 scope 的操作，不进入等待用户状态，而是按策略 fail 或 deny。
```

Profile 配置复用现有 settings，不引入新的 YAML 配置语言。P2 只支持 `interactive` / `guarded` / `full` 三个 builtin profile；`--permission <profile>` 引用其中一个 profile，并读取 `.magipi/settings.json` 或 global settings 中对应 profile 的 scope。

示例配置：

```json
{
  "taskrun": {
    "permissionProfiles": {
      "full": {
        "nonInteractive": true,
        "paths": {
          "allow": ["$WORKSPACE/**"],
          "deny": ["~/.ssh/**", "~/.aws/**", "/etc/**"]
        },
        "commands": {
          "allow": ["git", "python", "uv", "pytest", "npm", "pnpm", "node"],
          "deny": ["sudo", "rm -rf /"]
        },
        "network": {
          "mode": "allowlist",
          "allowHosts": ["github.com", "pypi.org"]
        },
        "git": {
          "allowCommit": false,
          "allowReset": true,
          "allowRevert": true
        },
        "onUnapproved": {
          "mode": "fail_step"
        }
      }
    }
  }
}
```

这样用户可以表达：

```text
我授权你在这个 workspace 里全自动工作。
但不要碰系统目录，不要 sudo，不要自动 commit，也不要访问未显式允许的网络目标。
```

上述 JSON 是显式 scope 示例，不是默认值。默认 profile 是 `interactive`；在 non-TTY / headless 执行路径中必须 fail-fast。`guarded` / `full` 对超出 scope 的操作也应 fail closed，而不是让模型自由发挥。

### 3.6 Rehydration Summary

TaskRun 会自动生成稳定摘要，用于在后续执行中恢复上下文。

例如：

```text
TaskRun: optimize retrieval latency
Goal: reduce p95 latency without lowering accuracy
Current best: 183ms
Last attempt: changed chunk cache strategy, result regressed to 211ms, reverted
Current state: clean
Permission mode: full within workspace
Next action: inspect embedding batch size and benchmark impact
```

这个摘要不是长期记忆，而是当前任务的工作状态压缩。

它的作用是让 agent 在下一轮继续工作时，不需要重新读取全部历史。

### 3.7 TaskRun-owned Step Queue

P2 的任务推进由 magipi 自己的 Postgres task tables 承担。

第一版把 step 定义为 agent runtime slice，而不是人类项目管理 issue：

```text
一次输入
一段 agent 执行
一组工具调用和 audit event
一组 permission decision
一个明确结论
一个下一步建议
```

初版 queue 模型保持线性：

```text
task_steps.step_index 定义顺序
task_steps.status 定义 pending / running / done / failed / blocked / cancelled
task_runs.current_step_id 标记当前 step
taskrun next 从 Postgres 中读取下一步和 deterministic summary
```

P2 验收范围就是这条 Postgres-backed linear queue；`next` 查询只读取 TaskRun DB records 和 deterministic summary。

### 3.8 P3 Gateway Readiness

P2 不实现 Gateway，也不冻结公开 Gateway host API。

TaskRun core 可以在 `magipi` 进程内提供 CLI 子命令复用的内部 service seam。P3 启动时再从这些真实使用过的 seam 中抽取公开 host API contract。

例如：

```text
start
resume
step
run
cancel
status
summary
events
set_permission_profile
```

P3 Gateway 未来可以负责：

```text
接收外部请求
启动 TaskRun
展示 TaskRun 状态
推送 TaskRun 事件流
发送完成通知
处理 channel / UI / API 入口
```

但这些不属于 P2 交付。

P2 的目标是让 TaskRun 在 CLI / workspace 内独立可用，而不是提前绑定 P3 API 形状。

## 4. P2 交付里程碑

### Milestone 0: Architecture Contract

目标：在 M1 之前固化 TaskRun 的技术边界。

验收标准：

```text
P2 architecture 被审阅并 accepted
TaskRun truth / projection 边界明确
TaskRun 与 AgentSession 的关系明确
TaskRun status / budget / stop_conditions contract 明确
crash / process restart 后 stale running 可自动识别并落到 blocked
TaskRun-owned AgentSession 与用户 session 命令的边界明确
同一 workspace 同时只允许一个 running TaskRun
PermissionProfile 复用 P1 PolicyDecision contract
interactive profile 在 non-TTY / headless 下 fail-fast
PermissionProfile 只支持 interactive / guarded / full 三个 builtin profile
TaskRun step queue 由 Postgres truth 支撑
steer / follow-up / abort 在 TaskRun step 中的行为明确
compaction 后 TaskRun deterministic summary 注入位置明确
close / cleanup / archive 的 work-persisted 前置条件可程序化检查
Experiment ledger / summary / metric contract 明确
```

### Milestone 1: TaskRun Skeleton

目标：让 workspace 能创建和保存一个 TaskRun。

用户能力：

```text
创建 TaskRun
查看 TaskRun 状态
记录 TaskRun 事件
生成 TaskRun 摘要
关闭 TaskRun
```

示例命令：

```text
magipi taskrun start "Analyze this repo and identify the top 3 refactoring opportunities"
magipi taskrun status
magipi taskrun summary
magipi taskrun close
```

验收标准：

```text
TaskRun 状态可持久化
重启后可读取
summary 可作为下一轮上下文注入
事件日志可回放
DB schema migration 就位
workspace projection 可从 DB 重建
DB 不可用时 start/status/step fail-fast
stale running TaskRun 在 start/status/resume 前自动转 blocked，避免 workspace 永久锁死
```

### Milestone 2: Non-interactive PolicyDecision Resolver

目标：避免自动化任务卡死在用户确认环节。

用户能力：

```text
为 TaskRun 指定 permission profile
在 full permission 下自动批准范围内操作
在 guarded mode 下自动拒绝越界操作
所有 permission decision 可审计
```

示例命令：

```text
magipi taskrun start "Fix failing tests" --permission guarded
magipi taskrun start "Optimize benchmark for 5 rounds" --permission full
```

验收标准：

```text
工具不能直接等待用户确认
复用现有 PolicyRequest / PolicyDecision schema
所有 confirm 都经过 PermissionProfile resolver
full permission 下不会因为确认请求而停住
越权操作不会静默执行
permission decision 写入 audit 和 task event
headless 模式没有路径进入 TUI confirm
```

### Milestone 3: Manual Step Execution

目标：让用户可以手动推进一个 TaskRun。

用户能力：

```text
执行下一步
记录本轮 agent 行为
保存本轮输出
生成下一步建议
```

示例：

```text
magipi taskrun step
```

每个 step 应该产出：

```text
本轮目标
执行动作
工具结果
permission decisions
结论
下一步建议
```

验收标准：

```text
每个 step 有明确边界
step 结果可审计
失败 step 可记录错误
下一轮可以基于 summary 继续
```

### Milestone 4: Step Queue And Status Views

目标：让 TaskRun 的 step queue、历史和下一步查询由 magipi 自己稳定提供。

用户能力：

```text
查看 TaskRun 列表
查看 TaskRun history
查看当前 step 状态
查看下一步候选
看到 blocked / failed step 的原因
```

示例命令：

```text
magipi taskrun list
magipi taskrun history
magipi taskrun next
```

验收标准：

```text
所有查询从 Postgres TaskRun truth 读取
list = 当前 workspace 的 TaskRun records
history <id> = 指定 TaskRun 的 step timeline + key events
events <id> = 指定 TaskRun 的完整 task_events stream
linear step 的 next 查询 deterministic
pending / running / done / failed / blocked / cancelled 状态可见
blocked / failed step 有可审计原因
list / history / next 只读取 TaskRun DB records 和 generated summary fields
```

### Milestone 5: Bounded Auto Loop

目标：支持有限轮数的自动执行。

用户能力：

```text
启动最多 N 轮的自动推进
每轮执行后记录结果
达到停止条件后自动停止
失败次数过多时自动停止
```

示例：

```text
magipi taskrun run --max-steps 5 --permission full
```

停止条件包括：

```text
达到最大 step 数
任务完成
连续失败
连续 permission denied
permission denied 总数超限
用户取消
测试失败不可恢复
workspace dirty 状态异常
预算耗尽
```

验收标准：

```text
不会无限运行
每一轮都有日志
每一轮都有明确结论
用户可以随时查看和取消
non-interactive 模式不会等待用户输入
repeated block / deny 以明确 budget exit 停止
```

### Milestone 6: Experiment / Benchmark Loop

目标：支持类似 pi-autoresearch 的最小实验闭环。

这是通用 experiment kernel，不是 QMD autoresearch showcase 的最终报告/包装。QMD autoresearch 的 real A6000 lane、最终 README 和报告材料放到 P2 结束时再收口。

用户能力：

```text
运行 baseline
执行一次修改
运行 benchmark
解析 metric
比较结果
决定 keep 或 revert
记录实验结论
```

适用任务：

```text
性能优化
prompt 优化
retrieval 参数调优
模型 pipeline 调参
工具调用策略比较
```

每个 experiment 应包含：

```text
hypothesis
change
command
metric
result
decision
diff
permission decisions
```

验收标准：

```text
能比较 baseline 和新结果
能保留改进
能回滚退化
能解释为什么 keep/revert
能在 full permission 下连续运行多个 experiment
支持最小 METRIC name=value 输出协议
experiment records 作为 step 的 durable child records 保存
TaskRun summary 用确定性字段保留 current best / last attempt / next action
keep/revert 不会删除 TaskRun ledger
```

### Milestone 7: White-Box Runtime

目标：让 TaskRun 把 in-process agent loop 的白盒事件转化为任务质量信号，不再只读取最终回答。

依赖：`design_docs/roadmap/p2_taskrun_whitebox_runtime_supplement.md` 与 `design_docs/architecture/p2_taskrun_architecture.md` 待办 amendments D10-D15 必须先 accept；M7 是它们的实现里程碑。

用户能力：

```text
TaskRun step 不再只依赖模型自述判断完成
工具是否真的运行、是否成功有可查询证据
权限决策在 tool body 执行前可见可审计
中断/恢复后下一步建议基于真实过程事实
长 step 中的 compaction / auto-retry 不再隐形
```

验收标准：

```text
task_events 存 TaskRun-owned 派生事件（D10）
PermissionProfile resolver 已从 wrapper 迁到 before_tool_call hook（D11）
task_steps.output.verification_state 在 step finalize 时算出（D12）
TaskRunAgentSession adapter 替代 taskrun_runner 直接持 Agent（D13）
compaction / auto_retry 在 headless 路径有显式生产者（D14）
tool_execution_update 在工具执行期间实时可见，不再 buffered 后批量补发（D15）；
  测试覆盖：≥3 秒持续输出的工具其中间事件在执行中可被订阅者接收
最小可验收反例（supplement R4）由测试覆盖
```

### Milestone 8: P3 Gateway Readiness

目标：P2 不实现 Gateway，但 TaskRun core 要为 P3 保留可从真实用法中抽取的内部 service seam。

P2 只交付：

```text
稳定的 CLI-backed TaskRun service seam
可从 Postgres 重建的 projection 结构
可审计的事件记录结构
稳定的 permission policy / resolver 结构
稳定的 summary 生成逻辑
```

P3 才实现：

```text
Gateway
HTTP / WebSocket API
channel integration
远程启动 TaskRun
远程查看 TaskRun 状态
远程接收 TaskRun 事件流
通知系统
```

验收标准：

```text
TaskRun 不依赖 Gateway
P3 可以从 P2 内部 service seam 中抽取 host API
CLI 和本地 workspace 流程已经可用
```

## 5. P2 明确不做什么

P2 不做完整长期记忆系统。

TaskRun 保存的是 workspace-scoped task state，不是用户全局记忆，也不是人格记忆。

P2 不实现 Gateway。

Gateway 是 P3。P2 只保留 CLI 已实际使用的内部 service seam，不冻结公开 Gateway API。

P2 不追求完全无限自治。

第一版不会让 agent 无边界运行，而是先实现可控、可恢复、可审计的 bounded loop。

P2 不靠 prompt 解决权限问题。

“不要问用户，直接执行”不能只写在 system prompt 里。P2 必须在代码层实现 `PermissionProfile` / `PolicyDecision` resolver，否则自动化流程仍然会卡死。

P2 不做 LLM-driven cleanup。

TaskRun 关闭、清理、revert 或归档必须有明确的 work-persisted / work-discarded 证据，不允许模型用自然语言决定删除未持久化工作。

P2 不让 task state 文件成为 truth。

`.magipi/taskruns/` 是 projection / export。手工编辑不会绕过 Postgres truth、policy 和 audit。

P2 不把 TaskRun 自动写入长期 memory。

TaskRun summary、step conclusion 和 projection 文件都不是 memory truth。长期 memory 写入仍必须走 DB-backed memory tool 和审批路径。

## 6. P2 成功标准

P2 成功后，magipi 应该具备以下能力：

```text
一个复杂任务可以被启动为 TaskRun
TaskRun 状态能跨 session 保存，且恢复不依赖 workspace projection 文件
每一步执行都有记录
用户能随时查看当前状态
agent 能基于摘要恢复上下文
任务可以手动 step，也可以有限自动 step
TaskRun step queue 和 next 查询由 Postgres truth 支撑
benchmark/experiment 类任务可以形成最小闭环
permission profile 可以控制自动化程度
full permission 下不会因为确认请求而停住
Gateway 后续抽取点明确，但不依赖 Gateway
```

最关键的验收问题是：

```text
如果 agent 中断了，明天重新打开 workspace，TaskRun 是否能只从 Postgres 读出完整 state、step、event、summary，并知道任务做到哪里、为什么做到这里、下一步该做什么？
```

以及：

```text
如果用户选择 full permission，所有 tool confirm 是否都被 PermissionProfile resolver 自动解析为 allow/block/fail，并写入 audit/task event，而不是进入 TUI confirm 或等待用户输入？
```

以及：

```text
当 TaskRun step 已经收敛，用户能否仅凭过程事实（不依赖模型自述）判断 step 完成是否可信、当前 blocker 是什么、下一步建议是什么？也就是说：声称做完了但没有证据的 step，能否在 status / summary / next 视图中被自动标识为不可信？
```

如果这三个问题答案都是肯定的，P2 就达到了核心目标。

## 7. P2 的一句话定位

**P2 TaskRun 让 NeoMAGI / magipi 从“能调用工具的 agent”升级为“能持续推进复杂任务的 workspace runtime”。**

它包含三件核心事：

```text
可恢复的任务状态
可审计的执行过程
可控的自动授权
```

Gateway 放到 P3。P2 要先把 TaskRun core 做扎实。
