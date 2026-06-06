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
  - `design_docs/decisions/0025-use-git-as-p3-attempt-workspace-lineage.md`
  - `design_docs/decisions/0026-keep-p3-attempts-inside-one-taskrun-session.md`
- Related reference:
  - [openai/parameter-golf](https://github.com/openai/parameter-golf)（anchor source, observed 2026-05-28）
  - [What Parameter Golf taught us](https://openai.com/index/what-parameter-golf-taught-us/)（背景解读, observed 2026-05-28）
  - [reference_mini_parameter_golf_budget](design_docs/references/reference_mini_parameter_golf_budget.md)
- Related execution notes:
  - `dev_docs/discussions/p3_m5_parameter_golf_smoke_revision.md`
  - `dev_docs/discussions/p3_m5_autonomous_research_workflow_retro.md`
  - `dev_docs/user_tests/p3_m6_magipi_autonomous_research_acceptance_runbook.md`
- Discussion stage: accepted，第一层（用户需求口径），不含 architecture / implementation。

### 2026-05-31 执行顺序修订

P3-M3 已补齐 attempt tree / artifact / trajectory 的底层语义后，后续执行顺序调整为：

```text
P3-M5 Autonomous Multi-Attempt Loop 先行
P3-M4 Execution Narrative Renderer 延后到 UI 设计稳定后实现
P3-M6 Hardening & Scope Review 最后收口
```

原因：

- M4 是只读展示层，依赖 UI 设计；过早实现容易把未稳定的界面假设固化成代码债。
- M5 是 P3 的核心能力验证：agent 是否能自主 propose → run → judge → next。
- M3 已提供 M5 所需的机器可读观测面：`p3_trajectory.tree`、`current_best`、`last_attempt`、`next_action`、parent attempt tree 和 artifact refs。
- 推迟 M4 不等于推迟可观测性；M5 必须继续通过 CLI、Postgres truth、`task_runs.summary.p3_trajectory` 和 records bundle 保持可审计。
- M4 之后应消费真实 autonomous traces，而不是基于假想 UI shape 设计 Renderer。

### 2026-06-04 自主研究流程修订

P3-M5 Parameter Golf smoke 验证了底层 loop、DB truth、records、seed truth、
parentage 和 closeout plumbing，但也暴露了一个产品层缺口：由 Codex 手动执行
runbook、人工在 Claude Code 与 Codex 之间转述审阅意见，并不能证明 `magipi`
自身完成了自主科学流程。

后续执行顺序修订为：

```text
P3-M6 magipi Autonomous Research Workflow 先行
P3-M4 Execution Narrative Renderer 延后到 UI 设计稳定后实现
P3-M7 Hardening & Scope Review 最后收口
```

原因：

- P3 的核心用户价值不是让工程代理手动跑实验说明书，而是让 `magipi` 自己读取
  skill / runbook，提出假设，请求外部审计，独立裁决审计意见，执行实验，并基于
  DB / records truth 决定继续、停止或先修 infra。
- Claude Code 可作为 read-only auditor，但不能成为 controller；审计意见必须由
  `magipi` 独立 adjudicate 后才进入计划或实现。
- default research skill 是下一步最小产品形态：skill 负责过程契约，TaskRun /
  extension / harness 负责确定性执行和证据持久化。
- 候选池 / 遗传式提案仍是未来讨论，不进入 M6 验收；M6 只要求一轮 bounded
  scientific workflow 加最小 evidence-driven iteration。

### 2026-06-06 驱动循环修订

M5 暴露的问题分成两层，不能混在 workflow graph 里：

```text
procedural drive:
  让流程持续前进，不绕过审计/证据/gate，不靠模型自述完成。
  ADR-0027 workflow graph 负责这层。

optimization drive:
  从上一轮 metric/verdict 归因失败，提出更有信息量的下一轮假设，
  并按 stop policy 决定继续、停止或先修 infra。
  这层来自 proposer + trajectory feedback + strategy analysis + stop policy；
  graph 本身不是 drive。
```

因此 M6 不只验流程纪律，还必须验最小优化驱动：至少一次后续 attempt
必须结构化引用前一轮 evidence，并据此调整假设。metric improvement 不是 M6
必需，但没有 informed iteration 的负结果不能作为 M6 通过证据。

---

## 0. P3 目标

P1 交付 magipi 作为 coding agent 的底盘，P2 把它升级为可恢复、可审计、可控自动授权的 workspace runtime。P3 要验证的下一个能力是：

```text
magipi 能不能在低成本条件下，自主闭合一个 evidence-driven 实验循环：
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
目标不是冲 leaderboard，是验证 `magipi` 能自主闭合 evidence-driven 实验循环。
不做原始挑战要求的 8xH100 / 10min 规模，用单卡 A6000 mini budget。
"mini" = 用 repo 现成的 knob 调小规模，不重写框架、不重建 train_gpt.py。
MLX smoke 只用于开发和管线联调，不参与跨 attempt metric 判断。
"超过 baseline" 只在同一 A6000 mini budget 下比较。
激进架构搜索不是机器可完全判定的成功/失败指标；P3 优先使用机械停止条件
（预算耗尽、连续无显著改善、Tier 升级、触碰 val、artifact 超限、超时），
必要时保留 human scope review 作为越界兜底。
P3 terminal outcome 由 M7 关闭：
  success = magipi 在固定 mini budget 下自主超过同 budget 的 naive baseline，
            且产出可复现 artifact；
  stop_negative = magipi 完成足够的 evidence-driven iterations 后，按 stop policy
            证明该方向不值得继续投入。
M6 只证明自主 workflow + 最小优化驱动，不单独关闭 P3 anchor。
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
1. 我能让 magipi 在 Mini Parameter Golf 上自主推进实验，朝 objective metric
   做 evidence-driven iteration，并在 M7 给出 success 或 stop_negative。
2. 我能读懂 magipi 是怎么一步步推进的——它提了什么假设、跑了什么、
   结果如何、判定真假、下一步要试什么，而不是面对一堆 raw train log。
3. agent 能在 parameter-golf 这个强约定 repo 里按它的规矩工作
   （产出合规的 records/ 提交），不需要我把规矩硬编码进去。
4. 我能拿到一个明确的 artifact（16MB 提交：脚本 + 压缩模型 + log + README），
   它要么通过验证并刷新 best，要么被判定无效，状态清晰。
```

用户视角下 P3 MVP 应该回答：

```text
magipi 这次实验的假设是什么，改了哪些参数？
跑出来的 val_bpb 和 artifact size 是多少？
这是真的进步，还是 bug / 越界 / 不显著？
它接下来打算试什么，为什么？
当前 best 是哪一次 attempt，这棵实验树长什么样？
```

2026-06-04 后新增的用户需求口径：

```text
我不是要让人类在中间主持审阅和实验推进。
magipi 应该读取默认研究 skill / runbook，自主提出一个 bounded hypothesis，
调用 Claude Code 做独立审计，自己裁决审计意见，再执行实验。
实验后 magipi 要基于 DB truth / records / metric evidence 决定：
  继续跟进、停止该方向、还是先修 infra 偏差。
负结果也可以是有效结果；关键是科学流程自动完成且证据可审计。
```

候选池 / 遗传式提案是有价值的未来方向，但仍处于讨论阶段，不进入当前
M6 实践验收。

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

P3 anchor 的 metric 验证很直接，但下一步假设生成仍需要 strategy analysis：

```text
Actor          一个 writing agent，拥有自己的 TaskRun + workspace 写锁，
               负责提假设 / 改配置 / 跑实验 / 读 log / 形成下一步。
Strategy       生成环节的一部分，负责从 prior attempt 的 metric/verdict
               做失败归因、方向选择和 stop-policy 判断；可由同一 actor 完成，
               也可调用 read-only analysis agent 产出 evidence。
Metric Harness 环境，确定性，算 val_bpb + 跑机械验证
               （artifact size、统计显著性、是否触碰 validation set）。
               能机械验证的一律走脚本，不调 LLM。
Audit          独立 read-only 正确性审计，检查计划/证据是否存在 P0/P1
               blocker；它不是方向搜索器，也不是 controller。
```

口径要点：

```text
- 不需要 channel。strategy/audit 的交接是 (hypothesis, metric, log,
  attempt history) → evidence，是 structured handoff（task event + artifact），
  不是 message。
- 不做并发 peer（违反 P2 "single running TaskRun per workspace"，且重蹈 v1
  Agent Teams 的 worktree 冲突 / plan drift / restart context loss）。
- 颗粒度原则：TaskRun 始终属于一个 agent。若 strategy analysis 或 audit
  需要成为独立 agent，它用自己的 TaskRun 完成只读任务，与 actor 的 TaskRun
  分离，互不并发写同一 workspace。
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
- 让 magipi 在 Mini Parameter Golf 上自主闭合 evidence-driven 实验循环，
  并在 M7 给出 success 或 stop_negative。
- 把实验过程重组成可读的 Execution Narrative。
- 提供只读的 Execution Narrative Renderer（浏览器界面）。
- 把 16MB 提交作为一级 Artifact，状态（accept/reject）清晰。
- 用 Metric Harness 做确定性验证；按需调用 read-only strategy analysis 或 audit。
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
- 能机械验证的一律走 Metric Harness 脚本，不滥用 LLM strategy/audit。
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

### Path 3: 读懂实验叙事（UI 延后）

```text
Renderer 按 attempt 树 + hypothesis / metric / verdict 组织展示。
raw train log 默认折叠、可 drill down。
用户不读 raw log 即可回答 "best 是哪次、这次为什么没赢、下一步试什么"。
Renderer 只读，无写入口。
```

说明：P3-M3 是支撑 Path 3 的底层语义补齐，没有独立 user path。P3-M4 Renderer
推迟到 UI 设计稳定后实现；在此之前，M5 的可读性必须由 CLI / summary read model /
records evidence 保持，不允许为了跳过 Renderer 而降低审计性。

### Path 4: 自主多 attempt 循环

```text
agent 自主迭代：propose → run → judge → next，跨多次 attempt 构成树。
Metric Harness 做确定性验证；按需产出 read-only strategy/audit evidence。
本 path 是 M5 substrate：验证 attempt tree、metric ledger、current_best、
stop policy、records/closeout，不单独证明产品级自主研究。
```

### Path 5: 自主科学流程（skill + audit + adjudication）

```text
magipi 读取默认研究 skill / runbook / prior findings。
magipi 提出 bounded hypothesis 或实验计划。
magipi 调用 Claude Code 做 read-only audit，并保存 transcript。
magipi 独立裁决 audit findings，修订到无 P0/P1 blocker。
magipi 执行实验并基于 DB truth / records / metric evidence 作出
continue / stop_negative / fix_infra / blocked / success 决策。
magipi 至少完成一次 informed iteration：后一轮 proposal 明确引用前一轮
metric/verdict，并据此调整假设或停止策略。
```

说明：Path 5 是 2026-06-04 新增的产品级自主研究路径。它不要求 metric
一定改善；但负结果必须来自 informed iteration + stop policy，不能只是
一次失败后的自然语言结论。

---

## 5. P3 交付里程碑草案

编号代表语义边界。2026-06-04 后，P3-M3 之后的执行顺序为：

```text
M5 → M6 → M4 → M7
```

M4 延后期间，所有 M5/M6 产出的 truth 仍必须写入 TaskRun / `task_experiments` /
`p3_trajectory` / records bundle；不得引入临时 UI truth 或第二套 experiment ledger。

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
执行顺序：
  延后到 P3-M6 之后，等 UI 设计稳定并有真实 autonomous research traces 可供展示。

验收：
  Renderer 是 packages/webui 的 read-only 增量视图，继承 ADR-0024。
  按 attempt 树 + hypothesis / metric / verdict 组织展示。
  raw train log 默认折叠、可 drill down。
  用户不读 raw log 即可回答 best / 失败原因 / 下一步。
  无写操作入口；Renderer 不 own truth。
```

### P3-M5: Autonomous Multi-Attempt Loop

```text
执行顺序：
  P3-M3 之后优先实现；不等待 P3-M4 Renderer。

readiness / plan slice:
  开始 M5 coding 前先做一个很小的 readiness plan，锁定 autonomous loop 的
  输入、输出、停止条件、significance、strategy/audit checkpoint、anchor contract 和
  可观测性，不把 UI 设计或通用 skill 框架混入 M5。

验收：
  agent 自主迭代 propose → run → judge → next，构成 attempt 树。
  Loop 消费 M3 的 `p3_trajectory.next_action` 作为默认 base candidate，
    但真实 hypothesis / strategy 必须由 actor 生成并写入 attempt evidence。
  每次 attempt 的 truth 继续写入 `task_experiments`，进度汇总继续写入
    `task_runs.summary.p3_trajectory`；这是 M5 调试和后续 Renderer 的
    最低机器可读 run ledger。
  Metric Harness 确定性验证（size / 显著性 / 机械越界）全部走脚本。
  final significance session / repeated runs / Welch test 属于 M5。
  strategy/next-step evidence 必须写入 attempt evidence 或 trajectory summary；
    M5 不要求产品级自主 workflow，也不把 Codex/manual runbook 执行算作产品验收。
  M5 substrate 通过口径：attempt tree、metric ledger、current_best、
    records materialization、seed truth、parentage、closeout、loop stop policy 可审计。
  超 baseline + 可复现 artifact 是 P3/M7 anchor terminal success，不由 M5 单独关闭。
  停止条件触发即停；无法机械判定的 scope drift 走 human scope review 并记录 verdict。
  不新增 WebUI、浏览器写入口、第二套 runtime 或第二套 experiment ledger。
  可观测性通过 CLI、Postgres truth、`summary.p3_trajectory` 和 records bundle 保持。
  不在 M5 抽象通用 skill / anchor 框架；只允许一个很窄的
    `parameter-golf-mini` anchor contract，服务 prompt/context/harness/eligibility。
    等第二个真实 anchor 出现后再泛化。

2026-06-04 补充：
  M5 验收的是底层 multi-attempt loop substrate：attempt parentage、DB truth、
  records materialization、seed truth、current_best、closeout 和 loop stop policy。
  Codex 手动执行 runbook 或 proposal-file smoke 可以作为 engineering evidence，
  但不能单独证明产品级 "magipi 自主研究流程"。该产品级验收转入 P3-M6。
```

### P3-M6: magipi Autonomous Research Workflow

```text
执行顺序：
  在 P3-M5 底层 loop/plumbing 可审计之后执行；不等待 P3-M4 Renderer。

目标：
  用 default 或 workspace materialized research skill 证明 magipi 自己能完成一轮
  bounded scientific workflow，而不是由人类或 Codex 手动主持流程。
  Beads dependency workflow 调研作为 P3-M6 graph 设计参考，见
  `design_docs/references/reference_beads_dependency_workflow.md`；这是参考，
  不是引入 Beads/Dolt 作为依赖。

验收：
  magipi 通过正常 skill discovery 读取 research skill / runbook / prior findings。
  magipi 创建或维护 code-visible workflow graph；机制细节由 ADR-0027 拥有。
    roadmap 只要求 graph 不停留在 prompt 计划中，且 code 能 enforce readiness/gates。
  magipi 自主提出一个 bounded hypothesis 或 experiment plan。
  magipi 通过 audit adapter 调用 Claude Code CLI 或等价 read-only auditor，
    并保存 prompt、输入引用、stdout/stderr、exit code、model、effort、
    elapsed time 和 transcript ref。
  magipi 对 Claude audit findings 写结构化 adjudication：
    accept / reject / modify / defer，并给出简短理由和 action ref。
  如果 auditor-assigned P0/P1 blocker 存在，magipi 先修订计划并请求复审；
    magipi 可以写 rebuttal，但不能自行解除 blocker；解除只能来自复审清除或
    human explicit override。
  magipi 通过 TaskRun / extension tools / governed execution surface 执行实验，
    直接 shell debug 不能替代 magipi 路径。
  M6 使用 single magipi conductor 和最多一个 active writing executor。
    read-only auditor / analysis agent 可作为证据来源；多个 writing agents
    同时领取图上独立任务推迟到 lease / workspace isolation 成熟后。
  magipi 基于 DB truth / records / metric evidence 决定：
    continue、stop_negative、fix_infra、blocked 或 success。
  findings 写入直接引用：skill path、audit transcript、adjudication record、
    TaskRun / records / DB truth、final decision。

优化驱动验收（gating）：
  除 metric-invalid `infra_fix` blocker 外，至少两个 attempts；后一轮 proposal
    必须结构化引用前一轮 evidence。
  proposal evidence 至少包含：
    prior_attempt_ref、observed_signal、failure_attribution、next_hypothesis、
    expected_effect、changed_from_prior、stop_policy_ref。
  必须展示一次 "negative signal → attribution/strategy update → next attempt"
    或 "negative signal → stop policy satisfied" 的完整闭环。
  该 gate 与 workflow/procedural gate 并列；不能用流程完整性替代。

通过口径：
  metric improvement 有用但不是必需。负结果、方向停止或先修 infra 都可通过，
  前提是流程由 magipi 自主完成、满足优化驱动 gate，且证据可审计。

非目标：
  不要求候选池、mutation/crossover、遗传式搜索或多 agent 协同进化。
  不允许多个 writing agents 并发修改同一 workflow workspace / records。
  不要求 P3-M5 Parameter Golf final acceptance。
```

### P3-M7: Hardening & Scope Review

```text
执行顺序：
  在 M6 自主研究流程、M4 Renderer 设计/实现收口后执行。

验收：
  anchor 能被判定 "magipi 自主闭合了实验流程且结果客观可复现"：
    terminal success 需要超过同 budget naive baseline；
    stop_negative 需要满足 M6 informed-iteration evidence 和明确 stop policy。
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
  多 writing agents 同时领取 workflow graph 节点

研究策略（discussion only）：
  候选池 / mutation / crossover / 遗传式实验提案
  多候选并行搜索与全局探索-利用调度

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
4. M6 default research skill 放在哪里、如何通过 workspace materialized skill
   discovery 被 magipi 正常读取，而不是由用户粘贴流程？
5. Claude Code audit adapter 如何调用、限权、计时、保存 transcript，并写入
   TaskRun / records / findings 的可引用证据？
6. magipi adjudication record 的结构放在哪里：TaskRun event、records artifact、
   findings doc，还是三者组合？
7. M6 typed workflow state graph 如何存储和重建：TaskRun events、records artifact、
   dedicated table/read model，还是最小组合？graph apply / ready derivation
   / gate outcome 是否需要 dedicated helper，参考
   `design_docs/references/reference_beads_dependency_workflow.md`。
8. workflow node lease / workspace write lock / records write lock 如果未来要支持
   多 writing agents，需要哪些最小字段和超时/释放规则？
9. human explicit override 在不引入写操作 UI 前提下，通过哪个 governed surface
   写成可引用 truth：CLI、TaskRun event、records artifact，还是三者组合？
10. Renderer 在 packages/webui 内采用什么最薄 read model / route / template？
11. strategy analysis 在本 anchor 到底需不需要 LLM？还是 Metric Harness +
   trajectory reducer + 一个验证脚本就够？
   （倾向：能机械验证的尽量不调 LLM）
12. 【保留讨论】actor-critic 协同进化与候选池 / 遗传式搜索：对于 QMD 类无法在
   单一全知模型里直接验证的复杂问题，critic 需要随 actor 一起成长，实验提案也
   可能需要 population-style search。此问题不在 P3-M6 解决，但其结论会反向影响
   verdict / artifact / research skill 语义是否要预留扩展点。
13. P3 完成后，下一阶段（知识记忆 / 控制面 / 协同进化）的决策依据是什么？
   即：跑完 P3 我们应该已经知道哪些事，才有资格决策这些后置项？
```

本文件固定 P3 用户需求口径与 MVP 边界。experiment schema 差量、
artifact metadata truth、WebUI read model、Metric Harness、audit/adjudication 机制、strategy/audit 调用机制，
进入第二层 architecture 与第三层 implementation。

---

## Opening Section: P3-M2 Artifact Read Model 泛化边界

P3-M2 当前实现的是 **Mini Parameter Golf 专用 artifact read model**，
不是通用 artifact registry，也不是任意可量化任务的通用 best reducer。

这一点是有意收窄，不是遗漏。M2 的目标是把 M1 已经产出的 Parameter Golf
attempt metadata 收口成稳定、可查、可解释的 read model，先跑通 artifact
一等对象的最小闭环。

当前 P3-M2 read model 明确绑定 Parameter Golf contract：

```text
metric_name: val_bpb
metric_direction: minimize
artifact_cap_bytes: 16,000,000
baseline_context: M0 A6000 naive baseline mean / sample std / n
records_ref: records/<attempt_id>
required_files: README.md, submission.json, manifest.json, train_log.txt, eval_result.json
required_dirs: submission
verdict_status: accepted | rejected | error
harness_fields: status, budget_comparable, required_files_ok
```

因此，如果未来换成另一个可量化任务，例如：

```text
metric_name: accuracy
metric_direction: maximize
artifact_cap_bytes: 50MB
records_ref: outputs/<run_id>
verdict_status: pass | fail
```

当前 P3-M2 projection 不会自动正确处理。它会显式读取
`metrics["val_bpb"]`，并按 Parameter Golf 的 minimize / 16MB / required files
/ harness contract 判定 eligibility 与 current best。

后续如果需要支持多个 anchor 或多个可量化任务，不应继续在 P3-M2 helper 里追加
ad hoc if/else，也不应优先设计 Python 抽象类层级。P3 的扩展点应优先是可审计、
可版本化、可由 TaskRun 调用的 skill / anchor contract，例如：

```text
Artifact Skill / Anchor Contract:
  anchor_name
  metric_name
  metric_direction
  artifact_cap_bytes
  baseline_context
  eligibility_rules
  records_manifest_checks
  renderer_projection_shape
```

Parameter Golf 应只是其中一个 concrete skill / anchor contract：

```text
parameter-golf-mini:
  metric_name = val_bpb
  metric_direction = minimize
  artifact_cap_bytes = 16,000,000
```

这个泛化点应在 M3/M4/M5 之后、或引入第二个真实 anchor 时再决策。不要在没有第二个
真实任务压力前提前抽象，尤其不要先做脱离 runtime / skill 调用语义的通用类框架，
避免把 M2 的清晰 Parameter Golf truth split 防线稀释成未验证的通用框架。
