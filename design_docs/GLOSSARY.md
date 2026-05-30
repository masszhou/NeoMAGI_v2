---
doc_id: 019d6457-9290-7ef5-be6b-40618a07a865
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-06T21:49:14+02:00
---
# Glossary

This glossary is the lightweight cross-language ontology for NeoMAGI design
docs. Canonical terms stay in English and should match code, database fields,
or accepted design vocabulary. The Chinese gloss is a translation aid, not a
second canonical name.

Authority order: accepted ADRs in `design_docs/decisions/` first, then
architecture/data-model/reference docs. Development-only local notes are not
long-term sources.

## TOC

- [Agent](#agent)
- [Session](#session)
- [TaskRun / Experiment Loop](#taskrun--experiment-loop)
- [Provider](#provider)
- [Gateway](#gateway)
- [Memory](#memory)

## Agent

| Canonical term (English, code-aligned) | 中文 gloss | One-sentence definition | Source ADR |
| --- | --- | --- | --- |
| `Agent` | 智能体 | A magipi runtime actor that receives context, calls tools/providers, and produces work under NeoMAGI governance. | ADR-0009, ADR-0023 |
| `Actor` | 实验执行智能体 | In P3, the main magipi agent that owns the experiment TaskRun, workspace write lock, attempt loop, and Metric Harness invocation. | ADR-0026; `design_docs/architecture/p3_experiment_loop_architecture.md` |
| `Critic` | 独立评审智能体 | An optional independent magipi agent with its own TaskRun and clean context used to challenge or confirm high-value P3 attempts. | ADR-0026; `design_docs/architecture/p3_experiment_loop_architecture.md` |
| `Principal` | 用户利益身份轴 | The runtime identity axis for the user interest NeoMAGI represents; it is not a workspace preference file or display name. | ADR-0008; P2 identity/binding design |
| `SOUL` | 受治理自我对象 | The governed identity/principle object describing who the agent is and what values it follows for the principal. | ADR-0008; growth governance docs |
| `SOUL.md` | SOUL 工作区投影 | A workspace-visible projection of the active `SOUL`; it is prompt context, not the final truth source. | ADR-0008 |
| `USER.md` | 用户偏好上下文 | Workspace context describing who the agent serves and how to adapt to that user, such as language, timezone, and communication preferences. | ADR-0008; workspace context design |
| `IDENTITY.md` | 展示身份名片 | Workspace context for presentation metadata such as name, role, and voice; it is not runtime identity or authorization truth. | ADR-0008; workspace context design |

## Session

| Canonical term (English, code-aligned) | 中文 gloss | One-sentence definition | Source ADR |
| --- | --- | --- | --- |
| `AgentSession` | 智能体会话 | The long-lived P1 session runtime that coordinates provider calls, tools, resources, compaction, and durable session state. | ADR-0009, ADR-0016 |
| `long-lived AgentSession` | 长生命周期会话 | A session intentionally reused across a TaskRun or P3 Experiment Session so context, provider cache affinity, and compaction behavior remain continuous. | ADR-0026 |
| `compaction` | 上下文压缩 | Session-level context compression that preserves visibility of deterministic summaries but never becomes TaskRun, metric, memory, or trajectory truth. | ADR-0026; `design_docs/architecture/p2_taskrun_architecture.md` |
| `provider-visible context` | provider 可见上下文 | Structured context injected into the next provider call, including deterministic TaskRun summaries and session context projections. | ADR-0016, ADR-0026 |
| `cache_affinity_id` | provider 缓存亲和 ID | A stable provider request affinity identifier derived from session semantics when needed for provider-side prompt cache behavior. | ADR-0016 |

## TaskRun / Experiment Loop

| Canonical term (English, code-aligned) | 中文 gloss | One-sentence definition | Source ADR |
| --- | --- | --- | --- |
| `TaskRun` | 任务运行实体 | A workspace-scoped, durable, auditable task runtime backed by Postgres and executed through one long-lived `AgentSession`. | ADR-0026; `design_docs/architecture/p2_taskrun_architecture.md` |
| `TaskRun step` | TaskRun 步骤切片 | A bounded semantic slice inside a TaskRun that records one prompt/continuation, events, tool use, audit evidence, and output. | `design_docs/architecture/p2_taskrun_architecture.md` |
| `task_events` | TaskRun 事件账本 | Append-only Postgres event ledger for TaskRun lifecycle, audit, white-box runtime observations, and P3 attempt lifecycle events. | ADR-0023; `design_docs/architecture/p2_taskrun_architecture.md` |
| `task_experiments` | 实验记录表 | Durable child records attached to TaskRun steps for hypotheses, changes, commands, metrics, results, decisions, and diff references. | ADR-0025, ADR-0026; `design_docs/data_models/task_experiments.md` |
| `Experiment Session` | 实验会话 | One Mini Parameter Golf objective run in P3; by design it maps to exactly one TaskRun and one long-lived AgentSession. | ADR-0026 |
| `Attempt` | 单次实验尝试 | One P3 change/run/evaluation unit represented by one `task_experiments` record, one `records/<attempt_id>/` bundle, and optionally one scoped Git commit. | ADR-0025, ADR-0026 |
| `Hypothesis` | 实验假设 | The reason an Attempt is expected to improve `val_bpb` or improve evidence quality. | ADR-0026; P3 architecture |
| `Config` | 配置/代码变更摘要 | The code/config diff summary and changed knobs for an Attempt, stored in `task_experiments.change` and mirrored in records metadata. | ADR-0025; P3 architecture |
| `Run` | 训练/评估运行 | The command, seed, timeout, environment, and data references used to execute an Attempt. | ADR-0025; P3 architecture |
| `Metric` | 指标 | Machine-readable measurements such as `val_bpb`, artifact bytes, train/eval seconds, and stop step. | ADR-0025; P3 architecture |
| `Artifact` | 实验产物 | The local records bundle and compressed model/code size evidence for an Attempt, referenced by manifest and Postgres metadata. | ADR-0025 |
| `records/<attempt_id>/` | attempt 产物目录 | A local self-contained P3 artifact bundle containing manifest, README, logs, submission metadata, and code/dependency references. | ADR-0025; P3 architecture |
| `manifest.json` | 结构化产物清单 | A Metric Harness generated manifest that records attempt identity, lineage, run metadata, metrics, artifact metadata, and verdict. | ADR-0025; P3 architecture |
| `Verdict` | 机器可检验结论 | The structured Attempt outcome, canonically `accepted`, `rejected`, or `error`, with reasons and optional significance evidence. | ADR-0026; P3 architecture |
| `Trajectory` | 跨 attempt 轨迹 | The current best, last attempt, and next action state rebuilt from Postgres truth and injected as deterministic TaskRun summary. | ADR-0026 |
| `current_best` | 当前最佳 attempt | P3 deterministic summary field for the best valid Attempt under metric direction, verdict/significance, and artifact validity rules. | ADR-0026 |
| `last_attempt` | 最近一次 attempt | P3 deterministic summary field exposing the most recent Attempt identity, hypothesis, and structured verdict status. | ADR-0026 |
| `next_action` | 下一步动作建议 | P3 deterministic summary field for host-validated next exploration guidance such as `hypothesis_seed`, `branch_to_explore`, and `rationale`. | ADR-0026 |
| `Metric Harness` | 确定性指标验收器 | A deterministic Python verifier that parses logs/artifacts, checks gates, computes metrics/significance, generates manifest, and writes structured verdict payloads without calling an LLM. | P3 architecture |
| `Git workspace lineage` | Git 工作区谱系证据 | Git commits and branches used to show workspace diff provenance for P3 attempts, while Postgres remains metric/verdict/trajectory truth. | ADR-0025 |
| `Branch` | 探索分支 | In P3, a Git workspace lineage branch, not a live magipi session fork and not the semantic parent truth. | ADR-0025, ADR-0026 |
| `baseline` | 基线 | A reference attempt or metric distribution used for comparison; for P3-M0 the A6000 naive baseline is the fixed Mini Parameter Golf anchor. | ADR-0025; `design_docs/references/reference_mini_parameter_golf_budget.md` |
| `candidate` | 候选结果 | A single-run or multi-run Attempt result being compared against the baseline under the same budget and artifact gates. | `design_docs/references/reference_mini_parameter_golf_budget.md` |
| `significance` | 统计显著性 | The structured evidence, such as run count, mean, standard deviation, and p-value, used for final P3 success verdicts when enough runs exist. | ADR-0025; P3 architecture |

## Provider

| Canonical term (English, code-aligned) | 中文 gloss | One-sentence definition | Source ADR |
| --- | --- | --- | --- |
| `Provider` | 模型服务提供方 | A model backend integration such as OpenAI or Anthropic, accessed through provider adapters rather than leaking provider-specific details into higher layers. | ADR-0017 |
| `Provider adapter` | provider 适配器 | The boundary that translates NeoMAGI requests, streaming events, usage, and provider-specific cache fields to/from a concrete SDK or API. | ADR-0017 |
| `Usage` | 用量统计 | NeoMAGI's normalized token/cost contract with `input`, `output`, `cacheRead`, `cacheWrite`, and `totalTokens`. | ADR-0016 |
| `cacheRead` | 缓存命中输入 token | Normalized count of provider-side prompt cache tokens read from cache rather than newly processed as input. | ADR-0016 |
| `cacheWrite` | 缓存写入 token | Normalized count of provider-side prompt cache tokens newly written into provider cache. | ADR-0016 |
| `prompt cache` | provider 侧 prompt 缓存 | A provider-side latency/cost optimization; NeoMAGI does not store prompt cache content as local product truth. | ADR-0016 |
| `cacheRetention` | 缓存保留策略 | The normalized option `none`, `short`, or `long` controlling whether and how provider-side prompt cache affinity is requested. | ADR-0016 |

## Gateway

| Canonical term (English, code-aligned) | 中文 gloss | One-sentence definition | Source ADR |
| --- | --- | --- | --- |
| `Gateway` | host-facing 运行边界 | The future host-facing interaction/runtime boundary; P2/P3 documents avoid freezing a public Gateway API before real usage proves it. | ADR-0018, ADR-0024 |
| `WebUI` | 浏览器操作界面 | The operator-facing browser surface in `packages/webui`, initially read-only over existing Postgres runtime state and not a new truth source. | ADR-0024 |
| `Renderer` | 执行叙事只读渲染器 | In P3, a read-only WebUI view that renders attempt tree, metrics, verdicts, records, and Git diff links from existing truth sources. | ADR-0024; P3 architecture |
| `Channel` | 外部交互通道 | A future Gateway communication surface such as chat or web transport; P3 anchor explicitly does not use channel, room, or message as experiment narrative units. | ADR-0018, ADR-0024; P3 architecture |
| `Host-facing API` | host 面 API | A future public API boundary extracted from proven TaskRun behavior, not a second orchestrator or replacement for TaskRun truth. | ADR-0018; `design_docs/architecture/p2_taskrun_architecture.md` |

## Memory

| Canonical term (English, code-aligned) | 中文 gloss | One-sentence definition | Source ADR |
| --- | --- | --- | --- |
| `Postgres truth` | Postgres 真源 | The rule that durable business state such as memory, TaskRun, experiments, metrics, verdicts, and audit evidence lives in Postgres, not in prompt text or workspace files. | ADR-0007, ADR-0008, ADR-0025 |
| `memory ledger` | 记忆账本 | The Postgres-backed write ledger that is the production truth for machine-written memory. | ADR-0008 |
| `workspace projection` | 工作区投影 | A human-readable file export or projection that can be rebuilt from truth and must not become a competing write source. | ADR-0008 |
| `retrieval projection` | 检索投影 | An indexed representation derived from memory ledger writes for search/retrieval, not an independent memory truth. | ADR-0008 |
| `projection rebuild` | 投影重建 | The process of regenerating workspace or retrieval projections from Postgres truth after drift, failure, or migration. | ADR-0008 |
| `prompt memory drift` | prompt 记忆漂移 | A failure mode where model narration or compaction text is mistaken for durable truth instead of being validated against structured records. | ADR-0008, ADR-0026 |
