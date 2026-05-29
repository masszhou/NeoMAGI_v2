---
doc_id: 019e703d-23fa-7410-9e24-b36e88cb0ca0
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-28T21:16:23+02:00
---
# P3 Roadmap: Autonomous Experiment Loop (Anchor = Mini Parameter Golf)

## 状态

- Status: accepted
- Date: 2026-05-29
- Roadmap: `design_docs/roadmap/p3_experiment_loop_mvp.md`
- Related roadmap: `design_docs/roadmap/p2_taskrun.md`
- Related roadmap supplement: `design_docs/roadmap/p2_taskrun_whitebox_runtime_supplement.md`
- Related architecture: `design_docs/architecture/p2_taskrun_architecture.md`
- Related data model: `design_docs/data_models/task_experiments.md`
- Related decisions:
  - `design_docs/decisions/0008-memory-truth-closure-postgres-with-workspace-projection.md`
  - `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md`
  - `design_docs/decisions/0020-magipi-workspace-and-global-resource-layout.md`
  - `design_docs/decisions/0021-workspace-materialized-skills-and-env-grants.md`
  - `design_docs/decisions/0023-agent-core-pi-mono-protocol-parity.md`
  - `design_docs/decisions/0024-introduce-webui-operator-surface.md`
- Related reference:
  - [openai/parameter-golf](https://github.com/openai/parameter-golf)（anchor source, observed 2026-05-28）
  - [What Parameter Golf taught us](https://openai.com/index/what-parameter-golf-taught-us/)（背景解读, observed 2026-05-28）
  - [reference_mini_parameter_golf_budget](design_docs/references/reference_mini_parameter_golf_budget.md)
- Discussion stage: accepted，第一层（用户需求口径），不含 architecture / implementation。

---

## 0. P3 目标

P1 交付 magipi 作为 coding agent 的底盘，P2 把它升级为可恢复、可审计、可控自动授权的 workspace runtime。P3 要验证的下一个能力是：

```text
magipi 能不能在低成本条件下，自主闭合一个实验循环：
提出假设 → 改动配置 → 跑实验 → 量化结果 → 判定真伪 → 迭代下一步，
直到在客观指标上达成可验证的目标，并产出可复现的 artifact。
```

P3 用**一个具体的 anchor case** 来验证这件事，全程围绕它展开，不做与它无关的平台/分发功能。

### 0.1 Anchor = Mini Parameter Golf

选 OpenAI 的 Parameter Golf 作为 anchor，因为它把实验闭环需要的一切都具备且**客观**：

```text
明确指标：val_bpb（FineWeb validation 上的 bits per byte，tokenizer-agnostic）。
可复现日志：训练脚本自动产出 train log。
artifact size 约束：16MB（code bytes + 压缩模型 bytes）。
大量可调参数：架构 / 量化 / tokenizer / 训练超参，实验空间丰富。
不需要主观判断产出质量：成败由 metric 决定，不靠人审内容。
低成本可跑：
  - loop / plumbing 开发可用 Apple Silicon MLX smoke 路径跑通；
  - 可比 metric 与最终验收走单卡 A6000（本机或云端）mini budget。
强约定 repo：records/ 结构 + submission.json + train log 格式 + 复现要求，
            天然适合验证 agent 在 opinionated repo 约定内工作的能力。
```

这是相比 QMD LoRA 调教的关键升级：QMD 需要主观判断生成内容质量，无法干净地判断 agent 是真的成功还是事后被合理化；Parameter Golf 有 val_bpb 作为 ground truth。

### 0.2 性质：验证能力，不是打榜

```text
目标不是冲 leaderboard，是验证 "agent 能自主闭合实验循环"。
不做原始挑战要求的 8xH100 / 10min 规模，用单卡 A6000 mini budget。
"mini" = 用 repo 现成的 knob 调小规模，不重写框架、不重建 train_gpt.py。
MLX smoke 只用于开发和管线联调，不参与跨 attempt metric 判断。
"超过 baseline" 只在同一 A6000 mini budget 下比较。
激进架构搜索不是机器可完全判定的成功/失败指标；P3 优先使用机械停止条件
（预算耗尽、连续无显著改善、Tier 升级、触碰 val、artifact 超限、超时），
必要时保留 human scope review 作为越界兜底。
成功 = agent 在固定 mini budget 下自主超过同 budget 的 naive baseline，
       且产出可复现 artifact。
```

### 0.3 与 P2 的咬合

这个 anchor 不是来测边角功能，它直接检验 P2 最关键的 critical path：

```text
P2-M6 的 experiment kernel 已经有 durable experiment ledger；
P2 deterministic TaskRun summary 已经保留 current best / last attempt /
next action。
Parameter Golf 是纯 metric-driven 的迭代任务，会直接压力测试这些字段在
多 attempt、跨 compaction、跨 resume 后是否仍能防止 metric trajectory loss。
artifact、Procedure 消费、execution narrative 在这个 anchor 上自然合流，
不需要为它们各造一个独立 demo。
```

### 0.4 信息组织参照（精简）

P3 的可读性需求受 Slack/Slock 信息组织方式启发，取的是原则不是具体单位：

```text
用 "人类理解任务推进的天然语义单位" 来组织信息，而不是按数据库字段堆 dashboard。
我们的核心单位是 task execution（任务推进流），不是 message（对话流）。
实验本身是一棵树（leaderboard 上每个提交都建立在前一个 stack 之上），
所以 "branch / attempt 树" 是一级语义单位——这是从 thread 借来的 fork 语义
（可分叉、可合并、可丢弃），但作用在 attempt 树上，不作用在 message 流上。
P3 MVP 把这棵树当作数据关系处理（attempt 带 parent 指针），
不引入 live session 分支的运行时机制（推迟，见 §7）。
```

### 0.5 明确后置

```text
基础设施类（缺乏现在决策依据，等 anchor 跑通后再选型）：
  常驻控制面 / 多机器绑定
  信息流方式选型（push / poll / event / WebSocket）
  浏览器端写操作（指令 / 配置 / 装 skill）
  channel / room / 外部 chat channel / node / voice / canvas

知识与记忆类（好想法，整体推到 P4 或之后，P3 不碰）：
  知识提炼 / curation / Strategic Curator
  记忆素材对比实验（Obsidian 手动链接 vs 开发日志 process 链接）
  Memory 子系统（schema / 索引 / 自动链接生成）

多 agent 协同进化（保留讨论，见 §2.3 与 §7）：
  actor 与 critic 一起成长改进，用于无法在单一全知模型里直接验证的复杂问题
  （如 QMD 类实验）。P3 anchor 验证直接，不需要它；推迟到 P4+ 决策。
```

---

## 1. 用户需求口径

用户要验证的不是"更多用例"，是**一个**实验闭环能被 agent 自主跑通且可读。

```text
1. 我能让 agent 在 Mini Parameter Golf 上自主推进实验，超过 naive baseline。
2. 我能读懂 agent 是怎么一步步推进的——它提了什么假设、跑了什么、
   结果如何、判定真假、下一步要试什么，而不是面对一堆 raw train log。
3. agent 能在 parameter-golf 这个强约定 repo 里按它的规矩工作
   （产出合规的 records/ 提交），不需要我把规矩硬编码进去。
4. 我能拿到一个明确的 artifact（16MB 提交：脚本 + 压缩模型 + log + README），
   它要么通过验证并刷新 best，要么被判定无效，状态清晰。
```

用户视角下 P3 MVP 应该回答：

```text
agent 这次实验的假设是什么，改了哪些参数？
跑出来的 val_bpb 和 artifact size 是多少？
这是真的进步，还是 bug / 越界 / 不显著？
它接下来打算试什么，为什么？
当前 best 是哪一次 attempt，这棵实验树长什么样？
```

---

## 2. 核心对象口径

### 2.1 Anchor Task = Mini Parameter Golf

```text
固定一个可复现的 mini budget：MLX smoke 预算用于开发 / 联调，单卡 A6000
  预算用于可比 metric 与最终验收；两者不能混用。
先建立同 budget 下的 naive baseline（repo 自带 baseline 配置）作为对照。
成功硬指标：agent 自主产出的 artifact 在同 budget 下以统计显著性超过该 baseline，
  且能在 records/ 文件夹里复现。
```

runtime 上一个 experiment session 映射到 P2 的一个 actor TaskRun（1 TaskRun =
1 long-lived AgentSession）。每个 attempt 复用 P2 `task_experiments`，作为
该 TaskRun step / step slice 下的 durable experiment record。P3 不引入第二套
task runtime。

这层映射继承 ADR-0023：`agent_core` 保持 Pi-mono protocol parity；P3 的实验树、
artifact metadata、verdict 语义只能落在 TaskRun / experiment read model 层，
不能通过改 `agent_core` 协议面来表达。

### 2.2 实验语义单位

全部客观、机器可查（这正是换掉 QMD LoRA 的原因）。P3 不从零设计实验
schema，而是复用 P2 `task_experiments`：

```text
Experiment（总目标：mini budget 约束下尽量降低 val_bpb；一个 actor TaskRun）
 ├ Attempt（一次实验改动；一条 task_experiments record；带 parent 指针，构成树）
 │  ├ Hypothesis -> task_experiments.hypothesis
 │  ├ Config     -> task_experiments.change / diff_ref
 │  ├ Run        -> task_experiments.command + train log content_ref
 │  ├ Metric     -> task_experiments.metrics（val_bpb / artifact_size）
 │  ├ Artifact   -> attempt 级 metadata + workspace records/<attempt_id>/ content_ref
 │  └ Verdict    -> task_experiments.decision + result（含显著性 / 越界原因）
 └ Trajectory（experiment 级 current best / last attempt / next action，
              复用 P2 deterministic TaskRun summary）

Branch 暂不作为独立运行时对象，由 Attempt 的 parent 指针表达成树（见 §0.4）。
```

P3 的真实 schema 差量限定为：

```text
1. attempt 间 parent 指针（表达实验树，不引入 live session branch runtime）。
2. val_bpb / artifact_size / content_ref 的可查询表达：
   第二层 architecture 决定是留在 JSON payload，还是提升为列 / read model；
   但 truth 必须仍来自 Postgres + workspace records/。
3. verdict 增加统计显著性、越界原因、accept/reject reason。
```

### 2.3 Actor / Critic / Metric Harness

P3 anchor 的验证很直接，默认采用 read-only critic checkpoint：

```text
Actor          一个 writing agent，拥有自己的 TaskRun + workspace 写锁，
               负责提假设 / 改配置 / 跑实验 / 读 log / 形成下一步。
Metric Harness 环境，确定性，算 val_bpb + 跑机械验证
               （artifact size、统计显著性、是否触碰 validation set）。
               能机械验证的一律走脚本，不调 LLM。
Critic         可选，仅在 actor "我觉得我赢了" 的 checkpoint 被调用，
               用干净 context 挑战结果。它评估的是机器查不了的部分：
               假设值不值得跑、为什么不及预期、是不是 bug 而非真实进步。
```

口径要点：

```text
- 不需要 channel。actor→critic 的交接是 (hypothesis, metric, log, attempt history)
  → verdict，是 structured handoff（task event + artifact），不是 message。
- 不做并发 peer（违反 P2 "single running TaskRun per workspace"，且重蹈 v1
  Agent Teams 的 worktree 冲突 / plan drift / restart context loss）。
- 颗粒度原则：TaskRun 始终属于一个 agent。若 critic 需要成为独立 agent，
  它用自己的 TaskRun 完成审阅任务，与 actor 的 TaskRun 分离，互不并发写同一 workspace。
- actor-critic 协同进化（用于无法单模型验证的复杂问题）保留讨论，不进 P3（见 §7）。
```

### 2.4 Execution Narrative + Renderer

把 attempt 树重组成人类可读的实验叙事，并以浏览器**只读**界面渲染。
Renderer 是 `packages/webui` 中的一个增量 read-only view，受 ADR-0024 约束；
它不是新的渲染 runtime，也不是新的 truth owner。

```text
Renderer 只做一件事：把 TaskRun / task_experiments / task_events 之上的
WebUI read model 渲染成可读的实验叙事。
默认展示 Hypothesis / Metric / Verdict 这层，raw train log 默认折叠、可 drill down。
用户能不读 raw log 就回答 "当前 best 是哪次、这次为什么没赢、下一步试什么"。
Renderer 只读，无任何写操作入口，不 own 任何 truth。
设计 Renderer 会反向逼出 task_experiments / task_events 缺的语义
（parent 指针、artifact metadata、显著性 verdict），从而改进底层 task 模型。
```

### 2.5 Artifact（16MB 提交）

```text
Artifact metadata 字段：(attempt_id, val_bpb, artifact_size, content_ref,
              verdict, created_at)
content_ref 指向 workspace 内的 records/ 提交目录，不进界面层内存。
"accept" 语义 = 通过 Metric Harness 验证且刷新 best；否则 reject 并记录原因。
Renderer 能展示 artifact 列表与当前 best。
P3-M2 需要决定 metadata 是 `task_experiments.result` 的结构化 payload，
还是新增 read model / table；artifact bytes 不进入 Postgres，不进入 WebUI 内存。
```

### 2.6 Procedure 消费

anchor 本身就是验证场：

```text
agent 能自动发现并遵守 parameter-golf 的约定（records/ 结构、submission.json、
train log 格式、复现要求），按其规矩产出合规提交——而不是把规矩硬编码进 agent。
skill / env grant 遵守 ADR-0021；未 materialize 的 skill 不进 available_skills。
```

---

## 3. 范围与职责边界

### 3.1 P3 做什么

```text
- 让 magipi 在 Mini Parameter Golf 上自主闭合实验循环，超过 naive baseline。
- 把实验过程重组成可读的 Execution Narrative。
- 提供只读的 Execution Narrative Renderer（浏览器界面）。
- 把 16MB 提交作为一级 Artifact，状态（accept/reject）清晰。
- 用 Metric Harness 做确定性验证；按需 spawn read-only critic。
- 验证 agent 在 parameter-golf 约定内自主工作（Procedure 消费）。
```

### 3.2 P3 不做什么

```text
- 不做控制面 / 多机器 / 信息流选型 / 写操作 UI / channel / 外部 channel。
- 不做知识提炼 / Curator / 记忆素材实验 / Memory 子系统（推 P4+）。
- 不做 actor-critic 协同进化（保留讨论，推 P4+）。
- 不构建第二套 task runtime（复用 P2 TaskRun）。
- 不引入 live session 分支运行时（attempt 树用 parent 指针表达）。
- 不冲 leaderboard、不做 8xH100 规模、不做激进架构搜索。
- 不重建 P2 experiment schema；P3 只补 Mini Parameter Golf anchor 暴露出的
  parent / artifact / significance 差量。
```

### 3.3 硬约束（anti-truth-split）

```text
- Renderer 不 own 任何 truth，只读 WebUI read model + workspace records/。
- Artifact content 只在 workspace records/，不进界面层，不进 Postgres 大字段。
- Experiment session 是 1 actor TaskRun；attempt 是 task_experiments record。
- 可比 metric 预算全程固定为单卡 A6000；MLX smoke 不参与 accept/reject。
- 能机械验证的一律走 Metric Harness 脚本，不滥用 LLM critic。
- 越界优先用机械规则判定：validation 数据触碰、artifact 超 16MB、训练超时、
  改动 budget / tier、连续无显著改善、未授权 Tier 3 升级。
- "激进架构搜索" 这类无法完全机械判定的边界，显式保留 human scope review；
  其结论必须写入 verdict reason，不能隐含在模型自述里。
```

---

## 4. MVP 用户路径

### Path 0: anchor 定标 + 叙事语义（零代码）

```text
固定 mini budget（机器 / 路径 / shard / 迭代），建立 naive baseline。
写明成功硬指标（超 baseline + 可复现）。
确认 §2.2 实验语义单位，列出 P2 `task_experiments` 差量缺口。
```

### Path 1: 单次 attempt 闭环

```text
agent 完成一次完整 attempt：hypothesis → config diff → run → metric → 记录 artifact。
本阶段允许手动触发。
产出在 records/ 下合规、可复现。
卡点（如有）能定位到具体 step。
```

### Path 2: artifact 状态闭环

```text
artifact 有 val_bpb / size / verdict / content_ref。
通过验证且刷新 best → accept；否则 reject 并记录原因。
artifact content 不在界面层。
```

### Path 3: 读懂实验叙事

```text
Renderer 按 attempt 树 + hypothesis / metric / verdict 组织展示。
raw train log 默认折叠、可 drill down。
用户不读 raw log 即可回答 "best 是哪次、这次为什么没赢、下一步试什么"。
Renderer 只读，无写入口。
```

说明：P3-M3 是支撑 Path 3 的底层语义补齐，没有独立 user path。

### Path 4: 自主多 attempt 循环

```text
agent 自主迭代：propose → run → judge → next，跨多次 attempt 构成树。
Metric Harness 做确定性验证；按需 spawn read-only critic。
成功：agent 在 mini budget 下自主超过 naive baseline，产出可复现 artifact。
```

---

## 5. P3 交付里程碑草案

### P3-M0: Anchor Setup & Narrative Vocabulary

```text
验收：
  单卡 A6000 mini budget 固定且可复现，naive baseline 建立。
  MLX smoke 仅用于 loop / plumbing 开发，不作为 metric 验收依据。
  成功硬指标写明（超 baseline + 可复现）。
  §2.2 实验语义单位 accepted。
  P2 `task_experiments` 差量缺口清单 accepted。
  明确 P3 不做控制面 / 写操作 / 知识记忆 / 协同进化。
```

### P3-M1: Single-Attempt Closed Loop

```text
验收：
  agent 完成一次 attempt（hypothesis → config → run → metric → artifact）。
  产出在 records/ 合规、可复现。
  复用 P2 TaskRun + task_experiments，不新建 runtime / experiment ledger。
  agent 在 parameter-golf 约定内工作（Procedure 消费基础验证）。
```

### P3-M2: Artifact as First-Class Object

```text
验收：
  artifact 有 val_bpb / size / verdict / content_ref。
  通过验证且刷新 best → accept；否则 reject 记录原因。
  artifact content 不在界面层。
  metadata truth 位置明确（task_experiments payload、read model 或独立表三选一），
    不产生 WebUI / workspace / DB 三套 truth。
```

### P3-M3: Experiment Semantics Backfill

```text
验收：
  复用现有 task_experiments 字段表达 hypothesis / config / run / metric / verdict。
  补齐 attempt parent 指针、artifact metadata、significance verdict。
  实验树可被重建为完整叙事（无语义丢失）。
  Trajectory 复用 P2 deterministic TaskRun summary（current best /
    last attempt / next action），并在多次 attempt 跨 compaction 后不丢 metric 轨迹。
  schema 变更不破坏 P2 TaskRun 既有验收。
```

### P3-M4: Execution Narrative Renderer (read-only)

```text
验收：
  Renderer 是 packages/webui 的 read-only 增量视图，继承 ADR-0024。
  按 attempt 树 + hypothesis / metric / verdict 组织展示。
  raw train log 默认折叠、可 drill down。
  用户不读 raw log 即可回答 best / 失败原因 / 下一步。
  无写操作入口；Renderer 不 own truth。
```

### P3-M5: Autonomous Multi-Attempt Loop

```text
验收：
  agent 自主迭代 propose → run → judge → next，构成 attempt 树。
  Metric Harness 确定性验证（size / 显著性 / 机械越界）全部走脚本。
  read-only critic 仅在 "自觉胜利" checkpoint 被调用，且用干净 context。
  成功：agent 在 mini budget 下自主超过 naive baseline 并产出可复现 artifact。
  停止条件触发即停；无法机械判定的 scope drift 走 human scope review 并记录 verdict。
```

### P3-M6: Hardening & Scope Review

```text
验收：
  anchor 能被判定 "agent 自主闭合了实验循环且结果客观可复现"。
  确认没有偷渡控制面 / 写操作 UI / 知识记忆子系统 / 协同进化 / 第二套 runtime。
  确认 task / artifact / event truth 没有分裂。
  删除未被真实路径使用的代码与接口。
  确认 Renderer 严格只读、不 own truth。
```

---

## 6. 暂不做

```text
基础设施：
  控制面 / 协议层 / 多机器绑定 / 信息流选型
  浏览器端写操作 / channel / room / 外部 channel / mobile / voice / canvas
  云端 relay / 多租户 SaaS

知识与记忆（P4+）：
  知识提炼 / curation / Strategic Curator
  记忆素材对比实验 / Memory 子系统 / 自动链接生成

多 agent（P4+）：
  actor-critic 协同进化（用于无法单模型验证的复杂问题）
  跨 agent task DAG / 全局 active skill / 完整 skill marketplace

运行时：
  live session 分支机制（P3 用 parent 指针表达 attempt 树即可）
  第二套 task runtime
  第二套 experiment ledger
```

---

## 7. 下一轮需要定的问题

```text
1. A6000 mini budget 已由 `reference_mini_parameter_golf_budget` 给出第一版；
   M0 需要实测 naive baseline 的 val_bpb 均值 / 方差。
2. 成功硬指标的显著性口径（多少次 run、什么阈值）如何与 A6000 mini budget 匹配？
3. task_experiments 差量（parent 指针 / artifact metadata / significance verdict）
   如何落到 schema 或 read model，且不影响 P2 TaskRun 既有验收？
4. Renderer 在 packages/webui 内采用什么最薄 read model / route / template？
5. critic 在本 anchor 到底需不需要 LLM？还是 Metric Harness + 一个验证脚本就够？
   （倾向：能机械验证的尽量不调 LLM）
6. 【保留讨论】actor-critic 协同进化：对于 QMD 类无法在单一全知模型里直接验证
   的复杂问题，critic 需要随 actor 一起成长。届时 critic 作为带自己 TaskRun 的
   独立 agent，与 actor 如何交接、如何避免并发写、如何共享/隔离 context、
   成长信号从哪来？此问题不在 P3 解决，但其结论会反向影响 P3 的 verdict / artifact
   语义是否要预留扩展点。
7. P3 完成后，下一阶段（知识记忆 / 控制面 / 协同进化）的决策依据是什么？
   即：跑完 P3 我们应该已经知道哪些事，才有资格决策这些后置项？
```

本文件固定 P3 用户需求口径与 MVP 边界。experiment schema 差量、
artifact metadata truth、WebUI read model、Metric Harness、critic 调用机制，
进入第二层 architecture 与第三层 implementation。
