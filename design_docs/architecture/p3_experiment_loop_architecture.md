---
doc_id: 019e7520-093c-70b4-9f9d-7d4bb900dc04
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-29T21:03:51+02:00
---
# P3 Architecture: Autonomous Experiment Loop

## 状态

- Status: accepted
- Date: 2026-05-29
- Document: `design_docs/architecture/p3_experiment_loop_architecture.md`
- Layer: 第二层（技术决策口径），对应第一层 roadmap，先于第三层 implementation
- Related roadmap: `design_docs/roadmap/p3_experiment_loop_mvp.md`
- Related reference: `design_docs/references/reference_mini_parameter_golf_budget.md`
- Related P2: `design_docs/architecture/p2_taskrun_architecture.md`
- Related decisions（既有约束）：
  - `design_docs/decisions/0007-postgres-truth-fail-fast.md`
  - `design_docs/decisions/0011-pi-mono-baseline-frozen.md`
  - `design_docs/decisions/0020-magipi-workspace-and-global-resource-layout.md`
  - `design_docs/decisions/0021-workspace-materialized-skills-and-env-grants.md`
  - `design_docs/decisions/0024-introduce-webui-operator-surface.md`
  - `design_docs/decisions/0025-use-git-as-p3-attempt-workspace-lineage.md`
  - `design_docs/decisions/0026-keep-p3-attempts-inside-one-taskrun-session.md`

---

## 0. 文档定位

本文档把 P3 roadmap 里的对象口径（Experiment / Attempt / Hypothesis / Verdict /
Artifact / Trajectory / Actor / Critic / Metric Harness / Renderer）落到具体的
技术承载：数据结构、存储位置、组件边界、调用协议、复用 P2 的哪些 seam。

不含：milestone 的具体实现步骤（第三层 implementation plan）。
不含：anchor 业务细节（见 reference_mini_parameter_golf_budget.md）。

---

## 1. roadmap 对象与技术承载的映射

```text
roadmap 对象              技术承载
─────────────────────────────────────────────────────────────────
Experiment Session       一个 long-lived TaskRun（复用 P2，不新建 runtime）
Attempt（attempt 树节点） 一条 task_experiments record + 一个 scoped git commit +
                         records/<attempt_id>/ 目录 + 若干 task_events
Attempt 树               task_experiments.parent_experiment_id 表达语义树；
                         Git commit graph 只表达 workspace lineage evidence
Branch（探索子线）        task_experiments.parent_experiment_id + Git branch；
                         P3 仅作为数据关系，不引入 live session 分支
Hypothesis / Config /     task_experiments 字段 / JSON payload + manifest mirror +
                         对应 task_events 生命周期事件
   Run / Metric / Verdict
Artifact（16MB 提交）     records/<attempt_id>/ 内的 train_gpt.py + 压缩模型 + log + README
Trajectory               runtime 从 Postgres truth 重算的 deterministic TaskRun
                         summary fields（current best / last attempt / next action）
Actor                    一个 magipi agent，在一个 TaskRun 里持续工作
Critic                   可选独立 magipi agent，自带 TaskRun，干净 context，
                         读取独立 snapshot / material bundle
Metric Harness            Python verification script，纯确定性，不调 LLM
Renderer                  packages/webui 只读增量视图，读 Postgres read model +
                         records/ + Git commit/diff links
```

**关键不对称**：roadmap 里 Experiment 是用户的目标层抽象，runtime 上对应一个长跑的
TaskRun；而 Attempt 是 TaskRun 内部的工作步骤，**Attempt 自己不是 TaskRun**。
这避开了 P2 "1 TaskRun = 1 long-lived AgentSession" 的硬约束。

**truth 分工**：

```text
Postgres task_experiments / read model
  语义 truth：attempt id、parent 指针、hypothesis、run metadata、metric、
  verdict、artifact metadata、current best / next action reducer。

task_events
  生命周期 / 审计 truth：start、tool/run、metric observed、decision、commit、
  critic invoke/return 等 append-only 事件流。

Git
  workspace lineage truth：代码 / 配置 / tracked records manifest / README / log
  摘要的 diff provenance。Git 不承担 metric / verdict / trajectory truth。

records/<attempt_id>/
  本地 artifact bundle：manifest mirror、train log、eval result、submission bytes。
  它必须自包含，但不是 Postgres metadata truth 的替代品。
```

---

## 2. TaskRun 与 Experiment Session 的对应

```text
1 个 Experiment Session = 1 个 TaskRun = 1 个 long-lived AgentSession
其中包含 N 个 Attempts（每次训练 + 评估 + 记录 = 1 个 Attempt）。
```

为什么不是 "1 个 Attempt = 1 个 TaskRun"：

```text
- 违反 P2 "single running TaskRun per workspace" 的硬约束
  （每个 attempt 一个 TaskRun 就需要支持并发或频繁切换）。
- 切断了 Trajectory 在 attempts 间的延续——P2 deterministic TaskRun summary
  fields 正是为 "在一个 long-lived session 内跨多次工作步骤保持 metric
  trajectory carrier" 设计的。每个 attempt 一个 TaskRun 等于把这个机制废了。
- 与 P2 已验证的 "long-lived session + compaction" 设计正面冲突。
```

含义：

```text
- agent 在一个 TaskRun 内顺序跑多个 attempts。
- 每个 attempt 写入 task_experiments；runtime 从 Postgres truth 重算 deterministic
  summary fields，并在下一步注入 provider-visible context。
- compaction 可以在 attempts 之间发生，但只负责让 deterministic summary 继续可见；
  它不是 trajectory truth，也不是 trajectory 更新点。
- TaskRun 启动 → 工作直到 session 验收硬指标命中 / 预算耗尽 / 触发停止条件
  → 关闭 TaskRun。这就是一个 experiment session 的完整生命。
- 跨 session 的状态延续靠 Postgres task_experiments / task_events truth +
  records/ + Git lineage，不靠 runtime 的 session 复活。
```

---

## 3. Workspace 布局与 Git 作为 workspace lineage truth

### 3.1 回答前置问题：git 必要吗？

**是的，必要**。理由：

```text
- attempt 的核心证据是 workspace diff：训练脚本、配置、records manifest、
  README / log 摘要。Git 是最低熵的 diff / rollback / review 工具。
- 每个 attempt 必须可复现。git checkout <sha> 把代码状态精确恢复，
  是最低成本的 reproducibility 实现。
- parameter-golf 上游 leaderboard 本身就是 PR-based（"On PR #1394 stack"），
  agent 学其他成功提交时，git diff 是最直接的输入。
- 不引入新工具栈。agent 通过 bash 调 git，无需新 skill 类别。
- git branch 可以辅助表达 "可分叉、可合并、可丢弃" 的 workspace lineage；
  语义树仍以 task_experiments.parent_experiment_id 为准（runtime 不引入分支，见 §3.4）。
```

### 3.2 Workspace 目录布局

```text
<workspace>/
├── .git/                              # workspace 整体被 git 跟踪
├── .gitignore                         # 屏蔽数据 / 大二进制 / .magipi
├── parameter-golf/                    # upstream repo clone（vendored 或 submodule）
│   ├── train_gpt.py                   # 每次 attempt 由 agent 修改
│   ├── data/                          # gitignored（FineWeb 数据，大）
│   └── records/
│       ├── attempt_0001/
│       │   ├── manifest.json          # 必须（schema 见 §4.3）
│       │   ├── train_log.txt          # 训练自动产出
│       │   ├── eval_result.json       # eval harness 输出
│       │   ├── README.md              # hypothesis + verdict（agent 写）
│       │   └── submission/            # 真正的 16MB artifact 目录
│       │       ├── train_gpt.py
│       │       └── model.bin          # gitignored 或 LFS
│       ├── attempt_0002/ ...
│       └── attempt_NNNN/
├── .magipi/
│   ├── session/                       # P2 session projection（非 truth）
│   └── taskruns/                      # P2 TaskRun projection（非 truth）
└── design_docs/, dev_docs/, ...       # P3 用不到，但 workspace 内可能有
```

### 3.3 Attempt 与 task_experiments / git commit 的一一对应

```text
每个 attempt 完成（不论成败）→ runtime 写 / 更新一条 task_experiments record，
并在 P3 attempt scope 内创建一个 git commit。

task_experiments 至少承载：
  id / task_run_id / step_id
  parent_experiment_id
  hypothesis
  change / command / metrics / result / decision
  diff_ref.records_ref
  diff_ref.commit_sha / branch / parent_commit

commit 落在以 attempt_id 命名的 branch 上：
  branch: experiment/attempt_NNNN
  commit message 模板：
    [attempt_NNNN] <one-line hypothesis>

    Parent: attempt_MMMM (or root)
    val_bpb: X.XXXX
    artifact_size: NNNN bytes
    verdict: accepted | rejected | error

    <multi-line README equivalent>

branch graph 与 attempt 树关系：
  不能等同。Git graph 是 workspace lineage evidence。

attempt tree truth：
  task_experiments.parent_experiment_id。

Git branch / commit graph：
  用于 diff review、checkout、rollback、人工理解 lineage。它必须能和
  task_experiments.diff_ref 互相校验，但不替代 Postgres semantic truth。
```

**好处**：Postgres read model 能稳定回答 current best / verdict / parent tree；
Git 能稳定回答 "这个 attempt 到底改了什么"。Renderer 读取 Postgres read model
展示 attempt 树，并链接到 Git commit / diff；`git log --graph --all` 只作为人类
辅助视图。

### 3.4 git branch ≠ magipi session 分支（关键警告）

```text
git branch：workspace lineage 分支。表达 "这次 workspace 改动基于哪个 commit"。
            P3 支持它作为数据证据，但 attempt parent 仍由 Postgres 记录。

magipi session 分支：runtime 层的 live session fork。表达 "在不污染主 session
            context 的前提下，试探一条子线"。
            P3 明确不引入（roadmap §0.4 + §6）。

含义：P3 的 agent 始终在一个 long-lived session 里顺序处理 attempts，
通过读 Postgres trajectory + Git diff/records 证据决定下一步去哪条 branch 推进。它不会
"fork 一个并行子 session 同时跑两条线"。如果某条 branch 的探索代价过高，
agent 可以保存当前状态、checkout 到别的 branch 继续——但仍是同一个 session
按顺序工作，不并发。

这一条必须写进 agent 的 system prompt，否则 agent 看到 "git branch" 字样
容易自作主张地尝试 fork session。
```

### 3.5 二进制 artifact 与 git 的处理

```text
16MB 模型二进制（每个 attempt 一份）累积起来会让 .git/ 膨胀。三种处理方式：

方式 A：gitignore 模型，仅 git 跟踪代码 + manifest + log
  优点：仓库小、agent 操作简单
  缺点：模型不在 git history 里，复现需要重新训练
  适用：P3 MVP 默认（重训成本可控：10 分钟一次）

方式 B：git-lfs 跟踪模型
  优点：模型在版本控制内、跨机器同步方便
  缺点：增加工具依赖、LFS quota
  适用：未来需要分享 attempt 树时

方式 C：模型放在 Postgres blob 或外部存储，records/ 里只保留指针
  优点：彻底解耦大文件
  缺点：增加存储抽象、与 records/ 自包含原则相悖
  适用：跨 workspace 共享 attempt 树时

P3 MVP 选方式 A。manifest.json 里记录 model_sha256 作为 artifact identity /
审计字段；复现性硬门槛是同配置、同预算、同 harness 下的 val_bpb / artifact_size
在允许容差内通过，而不是重训后模型 bytes 的 sha256 必须完全一致。
```

---

## 4. 数据结构

### 4.1 Attempt 在 task_experiments 中的表达

P3 不引入第二套 experiment ledger，复用 P2 已有的 `task_experiments` 表。
P3 schema / payload 差量优先落在 `task_experiments` 的 JSON 字段或 read model；
只有查询性能或约束需要时才提升为列。Attempt 的最小逻辑结构：

```text
task_experiments 字段      P3 语义
─────────────────────────────────────────────────────────────────
id                         attempt UUID truth
task_run_id / step_id       所属 actor TaskRun / step slice
hypothesis                  本次 hypothesis
change                      config_diff_summary, code_diff_paths, changed knobs
command                     train/eval command, timeout, seed, budget tier
metrics                     val_bpb, artifact_size_bytes, train_seconds, eval_seconds
result                      P3 verdict.status（accepted | rejected | error）、
                           reasons、significance、artifact metadata、
                           critic result summary
decision                    P2 decision vocabulary 的兼容承载；P3 accept/reject
                           语义由 result.verdict.status 表达，必要时在
                           implementation plan 中定义映射
diff_ref                    records_ref, commit_sha, branch, parent_commit,
                           parent_experiment_id
created_at                  attempt 创建时间
```

P3 必须补齐的语义：

```text
- parent_experiment_id：attempt tree semantic parent
- records_ref：workspace records/<attempt_id>/ 路径
- commit_sha / branch / parent_commit：Git workspace lineage evidence
- significance：n_runs / mean / std / p_value / threshold
- artifact metadata：content_ref / size / sha256 / reproduction command / env summary
```

### 4.2 task_events 生命周期事件

`task_events` 仍是 append-only event ledger，用于观察、审计和 projection rebuild。
它不是 attempt metadata 的唯一 truth。P3 在 P2 既有 `task_experiment_*`
事件族上扩展，而不是新增一套 `experiment.*` 命名空间：

```text
事件类型                              载荷主要字段
─────────────────────────────────────────────────────────────────
task_experiment_attempt_started        experiment_id, parent_experiment_id,
                                      hypothesis, branch
task_experiment_config_recorded        experiment_id, config_diff_summary,
                                      code_diff_paths
task_experiment_host_command_finished  experiment_id, phase, command, timed_out
task_experiment_metric_recorded        experiment_id, val_bpb, artifact_size,
                                      eval_seconds
task_experiment_decided                experiment_id, decision, reason,
                                      significance
task_experiment_commit_recorded        experiment_id, commit_sha, branch,
                                      records_ref
task_experiment_critic_requested       experiment_id, critic_task_run_id,
                                      material_bundle_ref
task_experiment_critic_returned        experiment_id, critic_task_run_id,
                                      agreement, concerns,
                                      suggested_verification
```

P3 task_events schema 在 P2 基础上的兼容性要求：

```text
- 不修改 P2 既有事件类型的 schema
- 新事件类型沿用 task_* / task_experiment_* taxonomy
- payload 走 JSONB，使用 per-event payload_version
- 不破坏 P2 既有 TaskRun 验收用例
```

与既有 `task_experiment_baseline_recorded` / `task_experiment_trial_recorded`
的关系：

```text
- 这两个既有事件保留为 P2 experiment loop / taskrun history 的 summary-grade
  事件，不被 P3 细粒度生命周期事件静默替代。
- P3 新增的 attempt_started / metric_recorded / commit_recorded 等事件是
  Tier 1 细粒度事件；implementation plan 需要明确它们与既有 summary 事件是
  双写、派生，还是按兼容层映射。
- 在 P2 兼容策略更新前，不能删除或重命名既有 baseline/trial 事件。
```

### 4.3 manifest.json schema

每个 records/<attempt_id>/manifest.json：

```json
{
  "schema_version": 1,
  "attempt_id": "attempt_0042",
  "parent_attempt_id": "attempt_0038",
  "task_run_id": "tr_...",
  "git_commit_sha": "abc123...",
  "branch_name": "experiment/attempt_0042",
  "created_by": "actor_agent_v1",
  "created_at": "2026-05-29T10:23:11Z",

  "hypothesis": "Replace SiLU with GeLU in MLP; expect 0.005 val_bpb improvement",
  "config_diff_summary": "train_gpt.py:142 SiLU->GeLU",
  "code_diff_paths": ["train_gpt.py"],

  "run": {
    "seed": 42,
    "max_wallclock_seconds": 480,
    "actual_train_seconds": 478.3,
    "tokens_seen": 67234112
  },

  "metric": {
    "val_bpb": 2.0934,
    "artifact_size_bytes": 15827344,
    "model_sha256": "...",
    "reproducibility_gate": "metric_tolerance"
  },

  "verdict": {
    "status": "rejected",
    "reasons": ["val_bpb worse than parent by 0.012 (not significant)"],
    "significance": {"n_runs": 1, "std": null, "p_value": null}
  }
}
```

**硬约束**：

```text
- manifest.json 必须在 attempt 结束时由 Metric Harness 生成（不是 agent 写文本）。
- agent 可以在 README.md 写自由文本叙述；manifest 是结构化数据。
- schema_version 用于未来演进，破坏性变更必须升版本。
- attempt_id 单调递增，全局唯一。
- model_sha256 是 artifact identity / audit 字段，不作为重训硬 gate。
- 复现性硬 gate 是同 budget / command / seed / env 下 Metric Harness 复验的
  val_bpb 与 artifact_size 在容差内成立。
```

### 4.4 Trajectory 字段（Postgres truth + deterministic carrier）

P3 直接复用 P2 deterministic TaskRun summary 字段作为 provider-visible
trajectory carrier，但 truth 来自 Postgres-backed `task_experiments` /
`task_steps` / `task_events`，不是来自 LLM compaction summary：

```text
current_best: { attempt_id, val_bpb, artifact_size }
last_attempt: { attempt_id, hypothesis, verdict_status }
next_action:  { hypothesis_seed, branch_to_explore, rationale }
```

runtime 在每个 attempt 完成后从 Postgres truth 重算这些字段，并在下一次
step start 注入 AgentSession context。Compaction 只负责在同一 long-lived
AgentSession 的 context 压缩后继续暴露这些 deterministic fields；它不是
trajectory 更新点，也不能覆盖结构化 truth。

这就是 P3 anchor 对 P2 设计的"考场"——见 P3 roadmap §0.3。

---

## 5. 系统组件

### 5.1 Actor

```text
身份：一个 magipi agent。
所有权：拥有 TaskRun + workspace 写锁。
能力：bash exec、file r/w、code edit、git 操作（全部走 P1 既有 coding tools）。
工作循环：
  1. 读 Trajectory（current_best / last_attempt / next_action）
  2. 形成本次 hypothesis 和 config diff
  3. runtime 创建 / 更新 task_experiments attempt record
     并写 task_event: task_experiment_attempt_started
  4. 修改 train_gpt.py（或对应文件）
  5. 写 task_event: task_experiment_config_recorded
  6. 调 bash tool 启动 torchrun（长跑工具，见 §7.2）
     必须显式传 timeout（见 §9.1）
  7. 写 task_event: task_experiment_host_command_finished
  8. 调 Metric Harness（同步函数调用）
  9. Metric Harness 生成 manifest，并把 metric / artifact metadata
     写回 task_experiments
  10. 写 task_event: task_experiment_metric_recorded
  11. 决策：Metric Harness 机械判定 accepted / rejected / error；
             必要时调 Critic checkpoint（如启用）
  12. 写 task_event: task_experiment_decided
  13. runtime 落地 records/<attempt_id>/ + scoped git commit，
      并把 commit metadata 写回 task_experiments.diff_ref
  14. 写 task_event: task_experiment_commit_recorded
  15. runtime 从 Postgres truth 重算 Trajectory carrier
  16. 回到 1
```

**Actor 的 system prompt 必须包含的硬规则**：

```text
- 不要尝试 FP8 训练（A6000 不支持）
- 不要修改 MAX_WALLCLOCK_SECONDS / val 路径 / 数据 split
- 不要默认升 Tier 3（H100），必须先请求并等批准
- git branch 是 workspace lineage，不是 session fork；attempt parent 以
  task_experiments.parent_experiment_id 为准
- 每个 attempt 必须以一次 runtime-scoped git commit 收口（即使失败）
- 不能跳过 Metric Harness
- 不能跳过 manifest.json 生成
- 不能把 Git log / manifest / README 当成 metric 或 verdict truth 的唯一来源
```

### 5.2 Metric Harness

```text
形态：纯 Python 验证脚本，运行在 actor 的 bash exec 环境内。
确定性：100% 不调 LLM，输入 → 输出可复现。
职责：
  - artifact_size_bytes <= 16,000,000（硬上限）
  - val_bpb 计算结果合理（区间检查 + 与官方 eval 输出比对）
  - 训练时间 <= MAX_WALLCLOCK_SECONDS（防越界）
  - 没有 val 数据泄漏（检查训练命令未指向 val 路径）
  - 显著性测试（如果 n_runs >= 3，跑 t-test 或等价统计检验，p < 0.01 阈值）
  - 生成 manifest.json
  - 写回 task_experiments.metrics / result / decision 所需的结构化 payload
返回值：verdict 结构 + 可机器读的 reasons 列表
```

**为什么 Metric Harness 不是 agent 的一部分**：

```text
- 它必须可独立运行、可被任何外部方（含人审）调用复验。
- agent 自评有动机偏好积极结论；Metric Harness 没有动机，只看数据。
- Renderer 可以展示它的输出，但不重新拥有 verdict truth。
- 在 CI / batch 复现验证时，没有 LLM 也能跑。
```

### 5.3 Critic Checkpoint

```text
形态：可选独立 magipi agent，自带 TaskRun，由 Actor / runtime 在高价值或
     边界不清的 attempt checkpoint 启动。
独立性：critic 的 TaskRun 与 Actor 的 TaskRun 完全分离，不共享 session、
       不共享 workspace 写锁。critic 读取独立 workspace snapshot 或
       material bundle 中的 records/ + manifest + Postgres read model export。
颗粒度：1 Critic invocation = 1 Critic TaskRun。完成后关闭。
```

调用契约：

```text
输入（写入 critic 的 initial context）：
  - 本次 attempt 的 manifest.json
  - 本次 attempt 的 train_log.txt
  - 父 attempt 链的 manifest 摘要（最多 N 级）
  - current_best 的 manifest
  - Actor 的自评 verdict

输出（critic 必须返回的结构）：
  {
    "agreement": "confirm" | "challenge" | "uncertain",
    "concerns": [str],
    "suggested_verification": [str]
  }

Actor 收到 critic 返回后：
  - confirm → 保持 Metric Harness 判定
  - challenge → verdict.status 仍保持 accepted/rejected/error 枚举之一，
                但 result.requires_followup_verification = true，
                suggested_verification 进入 next_action candidate
  - uncertain → verdict.reasons 附 critic 顾虑，必要时触发追加复测
```

**为什么 critic 是独立 TaskRun，不是 actor 的子调用**：

```text
- 颗粒度原则：TaskRun 始终属于一个 agent（roadmap §2.3 决策）。
- 隔离 context 污染：critic 的 context 应该只有结构化输入，没有 actor 的
  探索噪声、失败尝试、思考流水。独立 TaskRun 提供这个隔离。
- 不共享写锁：同一 workspace 只能有一个 running TaskRun；critic 必须使用
  独立 workspace_root / snapshot / material bundle，不能在 Actor workspace
  下并发运行第二个 writer。
- 可独立失败：critic 失败不破坏 actor 的工作。
- 可独立审计：critic 的 task_events 自成一段，方便事后审查 critic 是否
  在做实质评估而不是 rubber-stamp。
```

**MVP 范围控制**：

```text
- P3 MVP 默认 critic 可关。M5 验收以 "无 critic + Metric Harness only" 为基线。
- 一旦开启 critic，所有 critic invocation 都进 task_events，并把结果摘要
  写回 actor attempt 的 task_experiments.result。
- "actor-critic 协同进化"（critic 随 actor 一起改进）不在 P3 范围，见
  roadmap §7-Q6。
```

### 5.4 Execution Narrative Renderer

```text
形态：只读 web 界面（技术栈见 §9-Q1）。
数据来源（全部只读）：
  - Postgres read model：attempt tree、metrics、verdicts、current best、
    artifact metadata、critic summary
  - task_events（Postgres）：生命周期事件、审计、过程 drill-down
  - records/<attempt_id>/（workspace 文件）：manifest.json、README、log
  - git commit / diff links（workspace .git/）：workspace lineage evidence
不 own 任何 truth；不持久化任何 user-edited 内容。
```

渲染单位：

```text
顶层视图：experiment 树（Postgres read model）+ 当前 best + 进行中的 attempt
attempt 详情：hypothesis / config diff / metric / verdict / critic 反馈
              raw train log 默认折叠，drill-down 才展开
跨 attempt 对比：选 2 个 attempt 看 git diff + metric delta
```

**Renderer 限定再申明**：写操作（指令、配置、装 skill）不属于 Renderer，
全部留在 CLI/TUI。Renderer 只回答 "正在发生什么 / 发生了什么 / best 是哪个"。

---

## 6. 信息流（无 channel）

P3 anchor 不需要 channel / room。所有组件间通信走以下三类机制：

```text
1. 进程内同步调用（actor → Metric Harness）
   actor 的 bash exec 直接调 Python harness，等返回，读 verdict。
   零异步、零消息队列、零等待状态机。

2. 独立 TaskRun 间的握手（actor → critic）
   actor 写 task_experiment_critic_requested 事件 + critic 的输入材料路径
   → 等 critic TaskRun 完成
   → 读 critic 的 task_events / final summary 找 agreement / concerns /
     suggested_verification
   → 写 task_experiment_critic_returned 事件
   → 将 critic summary 写回 actor attempt 的 task_experiments.result
   actor 阻塞等 critic 完成；P3 MVP 不并发。

3. Renderer 的拉取式读取
   Renderer 读 Postgres read model + task_events 流（或定期轮询，技术栈决定）
   按需读 records/ + git commit / diff link
   零写回路径。
```

含义：**P3 没有任何 "message" 概念，没有 channel/room，没有 publish/subscribe
   逻辑除了 Renderer 的只读订阅**。这是 P3 §0.4 "task execution not message"
   的直接落地。

---

## 7. 必要前置能力（skills）

### 7.1 P1/P2 已提供，直接复用

```text
能力                            P1/P2 出处
─────────────────────────────────────────────────────────────────
bash exec（含长跑、超时控制）    P1 M5 coding tools + policy
file read / write              P1 M3 agent core
code edit（diff-based）         P1 M5 coding tools
long-lived session              P1 M6 durable session manager
compaction + deterministic     P1 M7 + P2 critical path
  summary fields
skills materialization          P1 M8 extensions/skills
PermissionProfile resolver      P2 M2
TaskRun runtime + recovery      P2 M1-M5
task_events truth + projection  P2 M1
task_experiments ledger         P2 M6 experiment kernel
PolicyDecision contract         P2 M2
```

### 7.2 P3 新增需要

```text
能力                            实现方式
─────────────────────────────────────────────────────────────────
git 操作 discipline             agent skill：每 attempt 一次 commit，
                                branch 命名规范，commit message 模板
manifest.json schema 生成        Metric Harness 内置，agent 不直接写
train_log 解析（val_bpb / size） Metric Harness 内置 regex/parser
显著性测试（t-test）             Metric Harness 内置（numpy/scipy）
长跑训练进程管理                 P2 bash exec 的 wallclock 边界 +
                                kill on overrun（可能需要 patch）
critic 独立 TaskRun 启动协议      可选新增：runtime 通过 magipi CLI/API 在独立
                                workspace snapshot / material bundle 上起 critic
                                TaskRun，等其完成
records/ 目录约定                agent skill：parameter-golf-conventions
                                .skill，包含 records/ 布局 + manifest 字段
Renderer（read-only web UI）     新增：技术栈见 §9-Q1
```

**约定**：所有 P3 新增 skill 走 ADR-0021 workspace-materialized skill 路径，
不进 provider-visible global available_skills。

### 7.3 明确不放入 P3 的能力

```text
从任意 GitHub repo pull 到临时目录、阅读后生成 magipi 兼容 skill 的
"skill creator / capability ingestion" 不属于 P3 anchor。

原因：
- P3 的 anchor 只需要 parameter-golf-conventions / parameter-golf-harness
  这类 workspace-materialized skill。
- 任意 repo ingestion 涉及网络出口、不可信内容、materialization 审批、
  secret / env grant、安全扫描和 ADR-0021 workspace skill 边界。
- 如果需要该能力，应进入 P4+ 或独立 ADR，而不是静默夹在 P3 architecture 内。
```

---

## 8. 与 P2 的契约（兼容性约束）

```text
不破坏 P2 既有约束：
- "1 TaskRun = 1 long-lived AgentSession" 保留（P3 是它的最大消费者）
- "single running TaskRun per workspace" 保留（Critic 是独立 workspace
  snapshot / material bundle，不与 Actor workspace 并发写——见开放问题 §9-Q3）
- Postgres 仍是 TaskRun / task_experiments / task_events truth，.magipi/ 仅 projection
- PermissionProfile 三档保留（interactive / guarded / full）
- deterministic summary 字段不修改 schema，只多用；compaction 不成为 truth

P3 新增对 P2 的扩展请求：
- task_experiments P3 payload/read model 差量（parent / artifact / significance /
  Git lineage metadata）
- task_events 类型扩展（见 §4.2）
- bash exec wallclock 上限 enforcement 必须可靠（若 P2 实现不完整需补）
- 启动独立 Critic TaskRun 的 API（可选，用独立 workspace_root / snapshot；
  不是子 TaskRun）——可能需要新增，见 §9-Q3
```

---


## 9. 开放技术问题

```text
Q1  Renderer 的最薄技术栈选哪个？
A1： 使用和 packages/webui 相同的技术栈，会同时在 claude design 平行推进前端设计

Q2  bash exec 的 wallclock 强制 kill 是否完整？
A2: 见 §9.1。

Q3  Critic TaskRun 与 Actor 共享同一 workspace 还是独立 workspace？
    - 共享同一 workspace_root：不选。会撞 P2 single running TaskRun per workspace。
    - 独立 workspace snapshot / material bundle：critic 工作目录单独，
      actor/runtime 把 records/、manifest、read model export 拷过去。
A3: P3 MVP 默认 critic 可关；若启用，使用独立 workspace_root / snapshot，
    不在 Actor workspace 下启动第二个 running TaskRun。

Q4  长跑训练（8-10 min）期间 agent 是否需要"挂起" TaskRun 让位给其他事？
    deterministic summary 是否需要在 attempt 进行中更新？
A4: 倾向：不挂起，单 session 串行；compaction 仅在 attempt 之间。

Q5  records/<attempt_id>/ 是否要做 schema 验证后才允许 commit？
A5: 倾向：Metric Harness 生成 + pre-commit hook 校验 schema。

Q6  attempt_id 分配策略：单调整数 / UUID7 / git short SHA？
A6: 倾向：单调整数 + zero-padded（attempt_0001），人类可读为主。

Q7  Tier 升级请求（A6000 → H100）的 UX 怎么走？
    候选：actor 写一条 task_experiment_tier_upgrade_requested 事件 + 暂停，
    人审通过后改 PermissionProfile 才能继续。
A7: 暂不考虑升级H100，如果P3验收通过，则考虑追加用Runpod

Q8  Metric Harness 用谁来维护其代码？属于 magipi、属于 anchor skill、
    还是放 workspace 内随版本演进？
A8: 倾向：放 anchor skill (parameter-golf-harness.skill)，按 ADR-0021
    materialize 到 workspace。
```

## 9.1 特别讨论的问题

```text
Q2  bash exec 的 wallclock 强制 kill 在 P2 当前实现里完整吗？若不完整，P3 是否需要先补 P2？
A2: 问题描述：agent 在每个 attempt 里通过 bash exec 启动一次 torchrun --standalone --nproc_per_node=1 train_gpt.py，并设了 MAX_WALLCLOCK_SECONDS=480。问题是这个 480s 是训练脚本内部的软上限——脚本自己会在到点时尝试退出。但脚本如果出 bug、卡死、CUDA OOM 没正常退出、或者陷入死循环，bash exec 必须外部强制 kill 整个进程树。

对P2实现的调查结果
结论：当前 magipi 实现是 Level 2，满足 P3 anchor 的最低要求
证据（kill 逻辑全在 policy/sandbox.py）
1. 进程组隔离 — sandbox.py:77 spawn 时设了 start_new_session=True。子 shell 成为新 session/进程组 leader，所以 process.pid == pgid，后面 killpg(process.pid, …) 才是对的、且只杀这个组。torchrun 及其 nproc_per_node worker 默认继承该 pgid（不自己
setsid），所以会被一锅端。
2. 到点先 SIGTERM 整组 — sandbox.py:157-166 _terminate_process_group：超时或 abort 触发 → os.killpg(process.pid,SIGTERM)，杀整个进程组而非顶层进程。killpg 抛非 ProcessLookupError 时回退 process.terminate()。
3. grace period — sandbox.py:136-149 _settle_process：asyncio.wait_for(wait_task, timeout=2)，等 2 秒。
4. 还在就 SIGKILL 整组 — sandbox.py:168-177 _kill_process_group：os.killpg(process.pid, SIGKILL)，回退 process.kill()。
这正好命中你定义的 Level 2：killpg(SIGTERM) → grace → SIGKILL，worker 一起死、GPU 释放。不是 Level 1（顶层 kill 留孤儿）。
没有的（Level 3 部分，符合预期不要求）：无 GPU 显存回收验证、无显式 zombie 收尾（靠 asyncio child watcher + process.wait()）无 cgroups 硬限制。
P3 anchor 必须注意的三个坑
坑 1（最致命）：默认 timeout = 120s；bash tool schema 当前上限 = 600s。
- shell_policy.py:14 DEFAULT_TIMEOUT_SECONDS = 120；shell_policy.py:15 MAX_TIMEOUT_SECONDS = 1500。
- bash.py:43 tool schema maximum 仍是 600，provider/tool argument validation 会先于 policy 卡住 >600 的调用。
- 你脚本软上限是 MAX_WALLCLOCK_SECONDS=480。如果 agent 不显式传 timeout，外部 kill 会在 120s 砍掉每一次训练——远早于脚本自己的480s 软退出。每个 attempt 都会在 2 分钟被腰斩。
- P3 必须显式传 timeout，设在 480 之上（如 500–540），让外部 kill 只当真正的兜底。P3 anchor 落在 600 内，当前可行；未来若需要 900s / Runpod 长训，必须修 bash.py:43 schema 与 policy 上限的一致性，光改 shell_policy 不够。
坑 2：grace 是 2s，不是你 Level 2 草图里写的 5s。
- 对单纯挂死的进程 2s 够。但对 CUDA/NCCL 收尾偏紧：worker 若 2s 内没响应 SIGTERM 就吃 SIGKILL，CUDA context 来不及优雅teardown。显存通常靠驱动在进程死亡时回收(一般没事),但如果你观察到 timeout-kill 之后下一个 attempt OOM,2s grace 就是头号嫌疑。是我唯一建议为 P3
调大的参数(5–10s,给 NCCL/torch 一个干净退出的窗口)。
坑 3:Level 2 逻辑有代码,但没有回归测试证明它真能收孤儿。
- 搜遍 tests/,没有一个测试 spawn 一个"自己再 fork 子进程"(torchrun 形状)的命令、然后断言子进程在超时后确实被杀test_taskrun_experiments.py:414-446 只是构造 HostCommandResult(timed_out=…) 的桩,从不走真实的 run_shell_command kill 径。
- 按你自己 CLAUDE.md 的工作规则("测试应约束真实用户路径和已知回归"),这恰恰是上线 P3 anchor 前该补的验收测试——因为失败模式(孤儿GPU 进程)是静默且烧钱的。
一个对 trajectory 数据有利的点
SandboxResult.timed_out 只在超时路径为 True,cancelled 对超时和 abort 都 True。所以"脚本自己到 480s 干净退出"(可能exit_code=0、timed_out=False)和"外部兜底开火"(timed_out=True)是可区分的。确保 P3 的 Metric Harness / task_experiments 写回层真的去读 timed_out,在它为 True 时不要信任
train_seconds。
---
一个范围说明:我查的是 magipi(实际运行的 NeoMAGI Python 实现),这是真正决定行为的代码。我没有去 diff pi-mono 在 97a38bf6 的 TS 版 bash 工具——ADR-0011 冻的是"上游参考基准是哪个 commit",而按 ADR-0009 NeoMAGI 走的是 contract 等价、不逐行兼容。如果你要的"pi-mono
基线本身是几档"做对照,我需要那份上游源码;但"实际跑 P3 的代码是几档"已经确定:Level 2。
```

---

## 10. 一句话总结

```text
P3 anchor 在技术形态上 = 一个 long-lived TaskRun (= experiment session) 内的
agent；每个 attempt 是一条 task_experiments record，并通过 records/<id>/ +
manifest.json 落盘，同时生成一个 scoped git commit 作为 workspace lineage
evidence；Metric Harness 做确定性验收，Critic 作为独立 workspace snapshot
上的可选 TaskRun 做怀疑性挑战，Renderer 只读地把 Postgres read model +
records + Git diff links 呈现成人可读的实验叙事。git branch 是 workspace
lineage，不是 runtime session 分支；trajectory truth 来自 Postgres，不来自
Git 或 compaction summary。
```
