---
doc_id: 019e1337-3641-754d-8b41-641acaa609f4
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-10T18:04:38+02:00
---
# QMD Autoresearch Mini Roadmap

## 一句话目标

这个 showcase 要证明：NeoMAGI 可以在一个具体 workspace 内，借鉴 Pi autoresearch
的事务内核，围绕 QMD Query Expansion Fine-Tuning 做可恢复、可审计、可回滚的实验循环。

第一轮不证明 QMD 模型质量变好。第一轮只证明一个最小闭环：

1. agent 修改一个小的训练脚本或配置；
2. 每轮都运行 `autoresearch.sh`；
3. 脚本输出 `METRIC name=value`；
4. extension 解析 metric；
5. agent 调用 `log_experiment`；
6. `keep` 时创建 git commit；
7. `discard`、`crash`、`checks_failed` 时 revert 本轮实验改动；
8. 历史写入 `autoresearch.jsonl`；
9. 重启后，agent 能从文件和 git history 继续。

报告里要强调的不是“NeoMAGI 已经优化出一个更好的 QMD 模型”，而是“NeoMAGI
已经可以把一个普通 workspace 变成带事务边界的 autonomous experiment workspace”。

## 用户故事

用户告诉 NeoMAGI：尝试优化 QMD 的 query expansion fine-tuning。

NeoMAGI 进入 QMD workspace，加载 workspace-local `.magipi` resources，初始化
autoresearch session，先跑 baseline，再提出一个小 hypothesis，修改一处代码或配置，
运行 benchmark，并把结果记录下来。

如果 metric 变好且 checks 通过，NeoMAGI 保留这次修改并提交。否则，NeoMAGI
撤销本轮非 autoresearch 文件改动，只保留 `autoresearch.md`、`autoresearch.sh`
和 `autoresearch.jsonl` 这些会话事实。下一次启动时，NeoMAGI 不依赖聊天上下文，
而是读取这些文件和 `git log`，继续下一个安全实验。

## 借鉴什么

从 Pi autoresearch 借鉴事务内核：

- `init_experiment`
- `run_experiment`
- `log_experiment`
- `autoresearch.md`
- `autoresearch.sh`
- `autoresearch.checks.sh`
- `autoresearch.jsonl`
- git keep/revert
- restart recovery

第一轮不借鉴完整产品面：

- TUI widget
- fullscreen dashboard
- browser export
- keyboard shortcut
- hooks
- `autoresearch-finalize`
- confidence UI

## Workspace 边界

当前 MagiPi 以 workspace 为资源发现和执行边界。这个 showcase 也应保持 workspace-local，
不要先把 autoresearch 抽成全局 core framework。

```text
showcase/qmd_autoresearch_mini/
├── ROADMAP.md
└── workspace/
    ├── .magipi/
    │   ├── extensions/autoresearch.py
    │   ├── skills/autoresearch-mini/SKILL.md
    │   └── prompts/autoresearch-next.md
    └── finetune/
        ├── benchmark.py
        ├── configs/baseline.json
        ├── data/
        └── evals/
```

这里的 extension、skill、prompt 只在该 workspace 或它的 scratch clone 中生效。
这正好展示 NeoMAGI 的项目局部能力：同一个 agent runtime 可以进入不同 workspace，
加载不同的实验协议。

## 第一轮 MVP 验收

第一轮不涉及真实 QMD 模型，不需要 GPU、网络、HuggingFace token，也不跑真实多小时训练。
它使用一个 deterministic QMD-shaped fixture，把事务语义先测透。

最小验收条件：

- `init_experiment` 创建或校验 `autoresearch.md`、`autoresearch.sh`、
  `autoresearch.jsonl`；
- agent 可以修改 `finetune/benchmark.py` 或 `finetune/configs/baseline.json`；
- 每轮实验都通过 `bash autoresearch.sh` 运行；
- benchmark 至少输出一行 `METRIC name=value`，主 metric 为 `score`；
- `run_experiment` 通过受治理的执行路径运行命令并解析 metric；
- 如果存在 `autoresearch.checks.sh`，benchmark 成功后、keep 前必须运行 checks；
- agent 对 `baseline`、`keep`、`discard`、`crash`、`checks_failed` 都调用
  `log_experiment`；
- `keep` 只允许在 scratch branch 上创建 git commit；
- `discard`、`crash`、`checks_failed` 都撤销本轮非 autoresearch 文件改动；
- `autoresearch.jsonl` 每轮追加一条完整 JSON object；
- 重启后，agent 通过 `autoresearch.md`、`autoresearch.jsonl` 和 recent git commits
  说明上一轮发生了什么、下一轮应尝试什么。

第一轮完成的标志：可以演示 baseline 加一轮 kept 或 discarded trial，然后模拟重启，
agent 不依赖聊天记录也能继续。

## 实现 Milestones

按 vibe coding 估算，实现建议切成 5 个 milestone。每个 milestone 对应一个主要上下文窗口
和一个可回滚 commit slice，按语义分工，避免把协议正确性、演示路径和报告材料混在一起。

### M0: 合同对齐

目标：只对齐用户口径和展示边界，不改核心实现。

本段只处理：

- Roadmap / README 级别边界；
- 第一轮验收目标；
- 明确 workspace-local 资源模型；
- 明确从 Pi autoresearch 借鉴什么、不借鉴什么；
- 明确真实 QMD 模型训练后置。

本段不处理：

- extension 逻辑修复；
- demo runbook；
- 真实 QMD adapter；
- 报告证据包装。

交付物：

- 本 roadmap；
- 后续实现能引用的 milestone 边界。

### M1: 事务内核修正

目标：聚焦 `.magipi/extensions/autoresearch.py` 和对应测试，把 autoresearch 的事务语义做准。

本段只处理：

- `METRIC name=value` parser；
- `init_experiment` / `run_experiment` / `log_experiment` 合同；
- JSONL schema；
- `keep` 创建 scratch commit；
- `discard` revert 本轮非 autoresearch 文件改动；
- `crash` / `checks_failed` 也走 revert 语义；
- scratch branch guard；
- artifact metadata 和 secret redaction；
- focused tests。

本段不处理：

- agent demo 流程是否好看；
- QMD 真实训练命令；
- 报告叙事。

交付物：

- extension contract 修正；
- parser、init、run、log、keep、discard、crash、checks 的回归测试；
- 一个协议层可接受的 commit slice。

### M2: Mini Demo 闭环

目标：把 deterministic mini workspace 跑成可演示的用户路径。

本段只处理：

- scratch branch 初始化；
- baseline run；
- 一轮小 hypothesis；
- 修改 `finetune/benchmark.py` 或 `finetune/configs/baseline.json`；
- 运行 `autoresearch.sh`；
- 根据 metric 和 checks 做 keep/discard；
- 展示 `autoresearch.jsonl`；
- 模拟重启并从 `autoresearch.md`、`autoresearch.jsonl`、`git log` 继续。

本段不处理：

- extension 大改；
- 真实 QMD 模型训练；
- 最终报告包装。

交付物：

- 短 runbook 或 README；
- 一份可复现的 mini demo 证据；
- baseline + one trial + restart recovery 的闭环。

### M3: QMD Adapter 映射

目标：把 mini loop 映射到真实 QMD fine-tuning 的实验面，但不把真实模型训练放进第一轮验收。

本段只处理：

- 读取当前 QMD `finetune/` 的真实入口；
- 定义哪些文件和配置允许 agent 修改；
- 把 QMD 的 train/eval/score 命令包装成 `autoresearch.sh` 口径；
- 定义真实 QMD 可输出的 metric 名称；
- 明确 GPU、网络、HF token、长时间训练都不是 Round 1 前提。

本段不处理：

- 证明 QMD 模型质量提升；
- 多小时 autonomous run；
- HF 发布或 GGUF conversion；
- dashboard 或 TUI。

交付物：

- QMD adapter/runbook 草案；
- 真实 QMD 命令到 `METRIC name=value` 的映射；
- 后续 real-QMD smoke 的准备清单。

### M4: 报告材料封装

目标：冻结实现范围，把成果包装成两天内可引用的报告材料。

本段只处理：

- showcase README / runbook；
- 用户价值叙事；
- Pi autoresearch 借鉴点；
- NeoMAGI workspace-local extension/skill/prompt 价值；
- mini demo 证据摘要；
- 代表性 `autoresearch.jsonl` 片段；
- 已完成和未完成边界。

本段不处理：

- 核心逻辑重构；
- 新增大功能；
- 真实模型质量承诺。

交付物：

- 报告可直接引用的 showcase 文档；
- 命令 transcript 或证据摘要；
- 下一阶段 real-QMD smoke 路线。

## 第一份报告不承诺

- 真实 QMD 模型质量提升。
- 多小时 autonomous training。
- HuggingFace 发布。
- GGUF conversion。
- dashboard 或 TUI polish。
- 在 workspace extension 证明足够前，把 autoresearch 泛化进 NeoMAGI core。

## 风险

- mini benchmark 可能显得过于玩具化。报告必须明确：Round 1 证明事务闭环，不证明模型质量。
- git 操作如果跑在默认分支上风险很高。keep/revert 必须限制在 scratch branch 或 scratch clone。
- restart recovery 不能偷偷依赖聊天历史。演示必须证明 agent 能从 `autoresearch.md`、
  `autoresearch.jsonl` 和 git history 恢复。
- 真实 QMD 训练依赖硬件、依赖安装和凭据。它们是 Round 2 环境约束，不应阻塞 Round 1。
