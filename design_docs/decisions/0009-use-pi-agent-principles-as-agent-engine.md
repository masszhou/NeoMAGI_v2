---
doc_id: 019dc3b0-37a4-731c-b42d-f097ef4e4679
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T10:09:51+02:00
---
# 0009-use-pi-agent-principles-as-agent-engine

- Status: accepted
- Date: 2026-04-25

## 选了什么
- 本项目智能体引擎采用 Pi Agent 的设计理念：小内核、显式工具、事件驱动、可组合分层，应用层掌控生命周期、策略和上下文装配。
- 引擎边界收敛为五个平面：
  1. Postgres state plane：承载 durable session、message、tool result、task、memory、provenance 和 audit。
  2. Provider adapter：统一模型消息、streaming、tool call、reasoning 参数、usage / cost 元数据。
  3. Agent runtime：编排 turn loop、tool orchestration、message queue、abort、retry、compaction 和生命周期事件。
  4. Tool / policy layer：所有工具以 schema 注册，并统一经过权限、sandbox、timeout、审计、结果裁剪和错误归一化。
  5. Application adapters：Gateway、channel、UI、workspace、memory 通过事件和自定义工具接入 runtime。
- LLM API 只作为计算单元使用：负责推理、生成和提出 tool call，不承载项目权威状态。
- 初始工具面保持小而稳定：context read、artifact write / edit、controlled exec / action、memory / task append / query。新增能力通过显式 tool、skill 或 prompt template 注入，不在 runtime 内置隐式业务流程。
- Memory truth 继续遵循 ADR 0008：Postgres ledger 是机器写入 memory 的唯一真源，智能体引擎只能通过受控工具读写 memory，不直接把 workspace Markdown projection 当成 truth。
- Provider-hosted thread、assistant memory、remote session state 不作为 truth 使用。
- Pi Agent 是设计先例，不是强制运行时绑定：NeoMAGI 不把自身锁定到 Pi CLI、Node.js monorepo、subprocess / RPC 模式或某个 pi-mono 版本。后续若直接复用 Pi SDK 或某个包，需要单独决议。

## 为什么
- Pi 官方架构把能力拆成 provider 抽象、agent core、application layer 等松耦合层；这与本项目希望把模型、工具、memory、gateway 解耦的方向一致。
- `pi-agent-core` 的核心定位是轻量 runtime：负责工具执行、message queue 和 streaming events，而不是把持久状态和业务流程塞进框架内核。
- Pi 的事件化模型适合 NeoMAGI：Gateway / UI 可以订阅 assistant、tool、lifecycle、compaction 等事件，而不需要阻塞式等待整轮结果。
- OpenClaw 的 Pi 集成案例说明，Pi 风格适合嵌入式 agent：应用可以自定义工具、提示词、session persistence、sandbox、provider 切换和事件处理，而不是把 agent 当黑盒。
- 与重型 graph / workflow 框架相比，Pi 风格降低了 MVP 阶段的概念数量和测试矩阵，把复杂性留在明确的 adapter、tool policy 和 memory contract 上。
- 该选择与既有决议一致：数据库 fail-fast、Postgres memory truth、workspace projection 都要求 agent runtime 明确边界，避免隐式降级和双主状态。

## 放弃了什么
- 方案 A：采用重型 agent graph / workflow 框架作为核心引擎。
  - 放弃原因：框架内建抽象过多，容易把 planner、router、memory、tool policy 混在一起，增加调试成本和架构漂移。
- 方案 B：直接把 Pi CLI 当成 NeoMAGI 的黑盒运行时。
  - 放弃原因：短期接入快，但 session、auth、tool policy、memory truth、sandbox 和 gateway lifecycle 会被 CLI 边界切开，不利于本项目自有约束。
- 方案 C：默认内置多智能体层级、自动 planner 和复杂 delegation。
  - 放弃原因：当前阶段优先建立可靠单 agent loop；多 agent 应作为 application-level adapter 或显式工具扩展，而不是 runtime 的默认复杂度。
- 方案 D：让每个业务能力绕过统一工具层直接接模型或数据库。
  - 放弃原因：会破坏权限、审计、sandbox、timeout、memory truth 和事件观测的一致性。

## 影响
- 智能体引擎实现时必须先定义 runtime event contract，再让 Gateway / UI / logs / tests 订阅事件。
- Provider adapter 必须可替换，不能把模型供应商细节泄漏到 tool 或 application adapter。
- Tool registry 是强边界：所有外部动作、workspace 修改和 memory 写入必须走注册工具。
- Session、tool result、compaction summary 等持续数据必须落 Postgres；runtime 内存态只服务当前运行。
- Compaction、abort 和 retry 需要可测试；测试重点放在 agent loop 行为和边界失败，而不是模拟完整 UI。
- 若未来引入 Pi SDK、OpenAI Agents SDK、LangChain、LlamaIndex 或其他运行时，只能作为 adapter / implementation detail，不能改变本决议定义的引擎边界。

## 参考
- Pi Monorepo: https://github.com/badlogic/pi-mono
- Pi Architecture Overview: https://www.mintlify.com/badlogic/pi-mono/concepts/architecture
- Pi Agent Core Overview: https://www.mintlify.com/badlogic/pi-mono/agent/overview
- OpenClaw Pi Integration Architecture: https://docs.openclaw.ai/pi
