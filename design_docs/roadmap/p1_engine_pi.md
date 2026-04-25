---
doc_id: 019dc386-956d-7325-8674-8ee9ddba5218
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T09:18:54+02:00
---
# P1 Pi CLI Product Roadmap

- Status: accepted
- Date: 2026-04-25

## 目标

用 Python 复刻 Pi CLI 的核心产品体验，把 `/Users/zhiliangzhou/devel/pi-mono` 中的 Pi coding agent 体系迁移为 NeoMAGI 的本地终端智能体产品。

P1 采用 **产品体验等价 + contract-stable** 路线：

- 产品体验等价：用户在 NeoMAGI CLI 中应获得与 Pi CLI 接近的核心工作流，包括 TUI 对话、slash commands、代码库工具、session、compaction、extensions、skills、settings 和结构化 session export。
- Contract-stable：优先保持 Pi mono 的 message、event、tool、extension、session entry 等核心 contract 可对照、可测试、可迁移。
- 不追求逐行实现兼容，也不承诺跟随 pi-mono 高频主线的每个修复或 UI 细节。

参考来源：

- Pi monorepo: https://github.com/badlogic/pi-mono
- 本地参考 clone: `/Users/zhiliangzhou/devel/pi-mono`
- 当前阅读基线：`main@97a38bf6`
- 基线核对时间：2026-04-25
- 优先复刻包：`pi-tui`、`pi-ai`、`pi-agent-core`、`pi-coding-agent`
- 第一阶段暂不复刻：`pi-web-ui`、`pi-mom`、`pi-pods`
- `badlogic/pi-share-hf` 是独立 repo，不在 pi-mono 内；P1 不内建 HF 上传或发布流程。

P1 的产品目标不是只做一个底层 agent engine，而是交付一个用户可以直接启动、持续使用、可扩展、可恢复 session、可导出结构化 session data 的 Python 版 Pi CLI。底层实现仍应尊重 NeoMAGI 已有数据库和 memory 决策：Postgres 是持久状态主平面，workspace Markdown 和 JSONL 文件是 projection / export / import 介质，长期 memory 写入必须通过受控 DB-backed tool。

## 产品定位

P1 Pi CLI 是 NeoMAGI 的本地终端主产品。

它面向用户提供一个 coding agent CLI：用户可以在终端中连续对话、搜索和修改代码、运行受控命令、加载扩展、使用 slash commands 管理 session，并把真实开发过程导出为结构化 session data。

它面向开发者提供一个可嵌入 Python SDK：应用可以创建 agent session，注册 provider、工具、extension、skill、prompt template、theme 和 UI adapter，订阅统一事件，并替换 session/backend/policy 实现。

它面向后续 NeoMAGI 系统提供一个 local agent shell：P2 Memory 和 P3 Gateway 可以复用相同 session、event、tool、extension 和 provider contract，而不是重新定义 agent 行为。

## 设计来源摘要

P1 复刻 Pi CLI 产品时，核心模块分为四层：

1. `pi-tui`：终端 UI 层。提供多行编辑器、slash command / 文件补全、markdown 展示、overlay、selector、settings list、loader、inline image、宽字符与中文 IME 支持。
2. `pi-ai`：模型与 provider 层。提供统一 message/content block、tool calling、stream event、reasoning level、image content、usage/cost、API key/OAuth、provider registry、model registry、faux provider 和跨 provider handoff。
3. `pi-agent-core`：agent runtime 层。提供 turn loop、assistant streaming、tool execution、tool result 回灌、parallel/sequential 工具执行、before/after tool hook、steer/follow-up queue、abort/continue、context transform 和事件订阅。
4. `pi-coding-agent`：完整 CLI 产品层。提供 AgentSession、SessionManager、ResourceLoader、settings、auth storage、model registry、built-in coding tools、slash commands、session resume/fork/tree、compaction、branch summary、extension、skills、prompt templates、themes、interactive / print / json / rpc modes 和 SDK 嵌入参考。

P1 不是把 Pi CLI 当黑盒运行时，也不是绑定 Node.js monorepo；目标是用 Python 复刻 Pi CLI 的产品语义和关键行为。具体实现可按 NeoMAGI 的 Python、Postgres、policy 和 memory 约束重做。

## 协议策略

- TUI 可以先行实现，但不能发明自己的 message / event 语义。
- P1 直接以 pi-mono 的协议为基线：`pi-ai` 的 `Message`、`ContentBlock`、`AssistantMessageEvent`，以及 `pi-agent-core` 的 `AgentEvent`。
- 早期 TUI 使用 mock playback 驱动，mock 数据必须按 Pi mono 协议结构组织；后续真实 `pi-ai` / `pi-agent-core` 接入时只替换 event source，不替换 UI contract。
- Python 复刻可以借用 Pi mono 的事件序列、session entry 结构和测试样例作为兼容性夹具。
- 序列化、Postgres 落库和 JSONL export/import 必须 opaque 透传 continuation 字段，包括 `thinkingSignature`、`thoughtSignature`、`responseId` 及 redacted thinking metadata。

## 用户价值

P1 完成后，用户应该可以把 NeoMAGI 当作一个可日常使用的本地 coding agent：

- 在 TUI 中连续对话，要求 agent 理解当前代码库并回答问题。
- 让 agent 搜索、读取、编辑文件，运行受控命令，下载公开 URL 指向的文件。
- 通过 `/resume`、`/tree`、`/compact` 等命令管理长 session 和分支上下文。
- 使用 skills、prompt templates 和 extensions 扩展 agent 行为。
- 在 session 中切换模型、reasoning level 和 provider。
- 导出可复放、可分析的结构化 session data；可选分享由后续外部工具承接。

## 第一阶段范围

### In Scope

- Python 复刻 `pi-tui` 的交互能力：
  - 多行 prompt editor；
  - prompt history；
  - slash command autocomplete；
  - `@` 触发的 fuzzy 文件搜索；
  - `!cmd` 和 `!!cmd` bash mode 的视觉状态与提交入口；
  - Shift+Enter 多行输入；
  - Ctrl+V 图片粘贴入口；
  - markdown/code block 渲染；
  - tool call / tool result 渲染；
  - overlay / selector / settings list；
  - loader / cancellable loader；
  - 中文 IME、宽字符、长粘贴的基本可用性。

- Python 复刻 `pi-ai` 的 provider 能力：
  - provider / model registry；
  - user / assistant / toolResult message；
  - text / image / thinking / toolCall content block；
  - streaming event protocol；
  - tool schema 与参数校验；
  - reasoning level；
  - prompt cache contract：`cacheRetention`、`sessionId`、provider-specific cache control / prompt cache key；
  - usage / cost schema：`input`、`output`、`cacheRead`、`cacheWrite`、`totalTokens` 与 5 维 cost；
  - opaque continuation 字段透传；
  - API key credential 管理；
  - 至少一种 OAuth provider；
  - faux / test provider；
  - 至少一个真实 provider adapter；
  - custom OpenAI-compatible model。

- Python 复刻 `pi-agent-core` 的 runtime 能力：
  - `Agent` / `AgentState`；
  - Python 命名映射：`wait_for_idle()` 对应 Pi `waitForIdle()`；
  - `prompt()` / `continue()` / `abort()` / `wait_for_idle()`；
  - turn loop；
  - sequential / parallel tool execution；
  - before / after tool call hook；
  - steer / follow-up queue；
  - transform context / convert to LLM boundary；
  - event subscription；
  - 每次 LLM call 前重新解析 API key 的 hook；
  - 每次 LLM call 透传 `sessionId` 和 prompt cache retention；
  - proxy stream 预留。

- Python 复刻 `pi-coding-agent` 的 CLI 产品能力：
  - `AgentSession` 与 session lifecycle；
  - session new / resume / fork / import / export；
  - session tree navigation；
  - branch summary；
  - manual / auto compaction；
  - context overflow recovery：识别 provider context-window overflow，compact 后重发；
  - settings 和 project/global config；
  - auth storage 和 model registry；
  - built-in coding tools；
  - slash command 系统；
  - interactive mode 核心入口；
  - print mode 单 prompt 输出入口，支持非 TTY / pipe / CI 场景；
  - SDK 嵌入入口；
  - resource loader；
  - extensions；
  - skills；
  - prompt templates；
  - themes。

- NeoMAGI-native 持久化与导出：
  - Postgres-backed durable session；
  - NeoMAGI 自有 Postgres schema version；
  - Pi JSONL import/export compatibility；
  - Pi historical session version migration / import strategy；
  - structured session export；
  - tool execution、message、compaction、branch summary、model_change、thinking_level_change 的结构化记录；
  - 基础脱敏/过滤策略占位。

### Out of Scope

- 不复刻 `pi-web-ui`。
- 不复刻 `pi-mom` Slack bot。
- 不复刻 `pi-pods` GPU pod 管理。
- 不交付 pi-package install/update/remove 子系统；`pi install npm:...`、`git:...`、local package 分发作为 P1 stretch 或后续阶段。
- `/share` 私有 GitHub gist 上传与 `pi-share-hf` Hugging Face 上传都不在 P1 核心交付；前者作为 stretch，后者作为外部工具。P1 只提供可被类似工具消费的 structured export schema。
- 不把本地 JSONL session 文件当作 NeoMAGI 生产 truth；它只是 import/export/projection。
- 不把 workspace Markdown memory 当作 memory truth。
- 不允许 extension 或 tool 绕过统一 tool registry / policy / audit 直接执行敏感动作。
- 不在 P1 内完成多租户 Gateway、Telegram/WebChat/Slack channel、principal binding。

### P1 Stretch

- sandbox bash extension 的完整隔离实现。
- json mode 与 rpc mode 的最小可用入口。
- GitHub Copilot、OpenAI Codex、Gemini CLI、Google Antigravity 等多套 OAuth subscription flow。
- cross-provider handoff 的完整兼容矩阵。
- pi-package 子系统。
- inline image 的 Kitty / iTerm / sixel 全协议等价。

## NeoMAGI 约束

完整 Pi CLI 产品可以作为 P1 目标，但实现必须遵守以下已有决策：

- PostgreSQL 是统一持久化数据面；session、message、tool result、compaction、audit 等 durable state 应落库。
- 数据库不可用时，生产/开发主路径应 fail-fast，不静默降级为只写本地文件。
- Memory truth 是 Postgres ledger；Markdown projection、session summary、custom message、extension entry 都不能自动成为长期 memory。
- Pi 默认 bash tool 直接 spawn shell；NeoMAGI 必须通过 before_tool_call / user_bash policy hook 增强默认行为，提供 sudo 拦截、path 白名单、timeout、truncation 和 audit。这是 NeoMAGI 强化项，不是 Pi 原生内建能力。
- 所有文件修改、shell、网络、memory 写入、task 写入都必须通过注册工具进入 policy、sandbox、timeout、审计和结果裁剪流程。
- Provider-hosted thread、assistant remote memory、模型供应商 session state 不能作为 NeoMAGI truth。
- Prompt cache 是 provider-side 优化和 usage/cost accounting 机制，不是 NeoMAGI durable state；NeoMAGI 只保存 cache retention 设置、provider cache affinity id 和 provider 返回的 cache read/write usage。

如果后续决定让 CLI 支持完全离线、无 DB 的 local-only mode，需要新增 ADR 修改数据库硬依赖决策。

## 产品需求

### R1. TUI 可用性

用户启动 NeoMAGI CLI 后，应看到一个可持续对话的 agent 界面，而不是一次性 stdin prompt。

验收口径：

- 支持多行输入、Shift+Enter 换行、历史输入、常用光标移动、长粘贴。
- 支持 `/` slash command 提示。
- 支持 `@` 触发的 fuzzy 文件搜索。
- 支持 `!cmd` bash mode 和 `!!cmd` 本地执行但不送入 LLM context 的模式提示。
- 支持 Ctrl+V 图片粘贴入口；P1 可降级为临时文件引用或 placeholder。
- assistant 文本以 streaming 方式出现。
- thinking、tool call、tool result、error、abort 都有清晰展示。
- overlay / selector 可用于 session、model、settings、confirm 等交互。
- 退出后终端状态正常。

### R2. 代码库问答与修改

用户可以在 CLI 中向 agent 提问当前代码库的问题，也可以要求 agent 做小范围代码修改。

验收口径：

- agent 能主动 search/read/list 当前代码库。
- agent 回答本地代码事实时必须基于工具结果。
- agent 能通过 edit/write 工具进行受控文件修改。
- 文件修改进入 session 和 audit 记录。
- 失败时以 tool error 反馈给模型，agent 能解释失败并提出下一步。

### R3. 受控命令与下载

用户可以要求 agent 执行本地命令，包括从 URL 下载公开文件。NeoMAGI 在 Pi 默认 bash 行为上增加 policy 层。

验收口径：

- 支持 `!cmd`：直接执行 shell，并将输出作为 `bashExecution` message 注入 LLM context。
- 支持 `!!cmd`：直接执行 shell，但 `excludeFromContext=true`，不送入 LLM context。
- `!` / `!!` 执行触发 `user_bash` extension event，可被 extension 拦截或替换 backend。
- sudo、破坏性命令、越权路径写入默认不可执行或需要确认。
- bash 输出被截断和结构化保存，完整输出按策略留存。
- URL download 只能写入允许路径。
- 网络和 shell 行为进入 tool execution / session / audit 记录。

### R4. Session 管理

用户可以长期使用 CLI，而不是每次从空上下文开始。

验收口径：

- 支持 new session。
- 支持 resume 历史 session。
- 支持 fork 当前 session。
- 支持 tree navigation。
- 支持 label / rename / delete 的基础能力。
- 支持 JSONL export/import。
- Postgres 是主存储，JSONL 可由 Postgres 重建或导入。
- JSONL import 支持 Pi `CURRENT_SESSION_VERSION` 和历史版本迁移策略。

### R5. Compaction 与 Branch Summary

用户长时间开发时，CLI 应能压缩旧上下文并保留关键工作状态。

验收口径：

- 支持手动 `/compact [instructions]`。
- 支持根据 token 阈值触发 auto compaction。
- 支持 context overflow recovery：捕获 provider context-window-overflow 错误后自动 compact 并重发请求。
- compact 后仍 overflow，或可压缩量低于安全阈值时，fail-fast 报告明确错误，不进入无限重试。
- compaction summary 记录目标、约束、进展、关键决策、下一步、关键文件。
- 支持 tree navigation 时生成 branch summary。
- compaction 和 branch summary 都进入 durable session state。

### R6. Extensions

用户和开发者可以扩展 CLI 行为，而不修改核心代码。

验收口径：

- extension 可以注册 tool。
- extension 可以注册 provider。
- extension 可以注册 slash command。
- extension 可以注册 keybinding，且不能覆盖核心不可让渡键位。
- extension 可以订阅 lifecycle / session / agent / tool / user_bash 事件。
- extension 可以拦截 session before-fork / before-switch / before-tree / before-compact。
- extension 可以注册 widget、footer、header、status line、overlay 和 custom tool renderer。
- extension 可以 replace system prompt 或 transform context。
- extension 可以通过受控 UI API 提示用户确认、选择或输入。
- extension 可以写入自有 state entry。
- extension 注册的 tool 必须走统一 policy。
- extension 失败不能让整个 CLI 崩溃。

### R7. Skills / Prompt Templates / Context Files

CLI 应支持 Pi 风格的可发现资源，用于注入项目规则和专用工作流。

验收口径：

- 支持全局和项目级 skills。
- 支持 `/skill:name` 命名空间。
- 支持 prompt templates。
- 支持 context files，例如 AGENTS.md 类文件。
- 支持资源 reload。
- 支持资源冲突和优先级规则：内建 command > extension command > prompt template > skill namespace。
- 注入内容是 prompt context，不是 memory truth。

### R8. Models / Auth / Settings

用户可以配置 provider、模型、reasoning level、API key / OAuth credential 和项目设置。

验收口径：

- 支持环境变量、配置文件和 runtime override 的 credential resolution。
- 支持 API key credential。
- P1 核心只要求交付一种 OAuth provider；其他 subscription OAuth flow 作为 stretch。
- 支持每次 LLM call 前重新解析 API key，以覆盖短期 token 过期场景。
- 支持 model list / selection / cycling。
- 支持 thinking level cycling。
- 支持 prompt cache retention 设置：`none` / `short` / `long`，并把 provider cache affinity id 传入；durable session id 到 provider cache affinity id 的映射策略由 architecture 明确。
- 支持 custom OpenAI-compatible model。
- settings 支持 global 和 project scope。
- 配置不提交真实凭据。

### R9. Structured Session Export

P1 必须从第一天开始收集结构化 session data，支撑调试、复盘、测试生成和后续可选外部分享。

验收口径：

- 每个 session 有稳定 session id、开始时间、cwd、模型信息。
- 每条 user / assistant / toolResult / bashExecution message 有 timestamp 和可序列化 content block。
- assistant message 记录 provider、model、responseId、stop reason、usage / cost、error message。
- usage / cost 必须保留 prompt cache read/write token 与 cost 维度。
- tool execution 记录 tool name、args、result metadata、is_error、duration、truncation 信息。
- `model_change` 与 `thinking_level_change` 是独立 entry 类型。
- compaction、branch summary、model_change、thinking_level_change 都可导出。
- 默认不自动发布到外部服务；分享是显式用户动作或外部工具能力。
- export schema 应可被 `pi-share-hf` 类工具消费，但 P1 不内建上传。

## 内建 Slash Command 基线

M0 行为矩阵必须枚举并分类 Pi 内建命令：

- Core P1：`/settings`、`/model`、`/scoped-models`、`/export`、`/import`、`/copy`、`/name`、`/session`、`/hotkeys`、`/fork`、`/clone`、`/tree`、`/login`、`/logout`、`/new`、`/compact`、`/resume`、`/reload`、`/quit`。
- P1 Stretch：`/share`。
- Informational / optional：`/changelog`。
- Dynamic commands：`/skill:name`、prompt template commands、extension-registered commands。

`/login`、`/logout` 在 P1 core 只承诺支持 P1 选定的 1 种 OAuth provider；多 provider picker 和多套 subscription OAuth flow 仍是 stretch。

`/clear` 不是 Pi 内建 slash command；清空 editor 属于快捷键行为，new session 使用 `/new`。

## 里程碑

Critical path：

`P1-M0 -> P1-M1 -> P1-M2 -> P1-M3`

并行/收口关系：

- `P1-M4` 依赖 `P1-M1` + `P1-M2` + `P1-M3`，负责 TUI/runtime integration。
- `P1-M5` 依赖 `P1-M3`，可与 `P1-M4` 并行推进。
- `P1-M6` 依赖 `P1-M3` + `P1-M5`，因为 session 需要保存 tool result。
- `P1-M7` 依赖 `P1-M5` + `P1-M6`，因为 compaction 需要跨 message、tool result 和 session tree。
- `P1-M8`、`P1-M9` 可与 `P1-M6` 并行推进。
- `P1-M10` 收口 structured export 和 replay fixture。

### P1-M0：Pi 基线和产品行为清单

产出：

- 固定 pi-mono 源码基线和参考文件清单。
- 明确 P1 采用产品体验等价 + contract-stable，不做逐行兼容。
- 列出 `pi-tui`、`pi-ai`、`pi-agent-core`、`pi-coding-agent` 的 Python 复刻对象。
- 提取 Pi mono 协议基线：
  - `pi-ai` message / content block / assistant stream event；
  - prompt cache contract：`cacheRetention`、`sessionId`、provider cache controls、usage normalization；
  - usage / cost schema；
  - opaque continuation 字段清单；
  - `pi-agent-core` agent lifecycle / turn / message / tool execution event；
  - `pi-coding-agent` session entry；
  - JSONL session version / migration 策略。
- 建立 Pi CLI behavior matrix：
  - 内建 slash command 全表；
  - mode：interactive / print / json / rpc；
  - SDK 嵌入入口；
  - built-in tools；
  - ExtensionAPI 全表；
  - skills / prompt templates / context files；
  - settings / auth / model registry；
  - compaction / branch summary。
- 建立 TUI mock playback fixture 目录。

完成标准：

- P1 明确是完整 Pi CLI 产品体验等价目标。
- TUI、provider、agent runtime 共享同一套 Pi-compatible event/message contract。
- NeoMAGI-specific 约束和 Pi 原生行为差异被列入 architecture 待拆分项。

### P1-M1：`pi-tui` Python Skeleton + Mock Playback

产出：

- 可启动的 NeoMAGI CLI TUI skeleton。
- 终端 lifecycle 和退出恢复。
- 多行输入、Shift+Enter、slash command、`@` fuzzy file search。
- `!` / `!!` bash mode 的 UI 状态与 mock execution display。
- Ctrl+V 图片粘贴入口。
- assistant streaming 渲染。
- thinking / tool execution 渲染。
- overlay / selector。
- cancel / quit / `/new` 命令入口。
- Pi-compatible mock event playback：
  - assistant text delta；
  - thinking delta；
  - tool_execution_start / update / end；
  - message_start / update / end；
  - bashExecution message；
  - turn_start / turn_end；
  - error / abort。

完成标准：

- 不接真实 provider 和 agent loop，也能播放一段完整 Pi-style session。
- TUI 消费的是 Pi-compatible event/message fixture，不消费自定义 UI-only 协议。
- 用户能看到和操作一个接近最终 CLI 的壳。
- TUI 退出后终端状态正常。
- Negative test：abort 后 UI 保留 partial/error 状态并恢复输入。

### P1-M2：`pi-ai` Python 核心

产出：

- 统一 message / content block / tool schema / stream event 类型，保持 Pi-compatible。
- provider registry 和 model registry。
- faux provider。
- 至少一个真实 provider adapter。
- usage / cost 五维 schema。
- prompt cache contract：`cacheRetention` (`none` / `short` / `long`)、`sessionId` affinity、provider-specific cache controls。
- opaque continuation 字段透传。
- cross-provider handoff 的 contract fixture。
- tool argument validation。
- API key credential 基础实现。

完成标准：

- 能用 faux provider 跑通 text、thinking、tool call、error、abort 的事件流。
- 能用真实 provider 完成一次 streaming 回复和一次 tool call。
- `pi-ai` 产生的 event 可以直接驱动 P1-M1 TUI playback contract。
- prompt cache fixture 覆盖 cache disabled、short retention、long retention、cache read/write usage normalization。
- 序列化 / 反序列化 / 落库 / 导出不丢 `thinkingSignature`、`thoughtSignature`、`responseId`。
- Negative test：cross-provider handoff 后下一轮回复不因 opaque 字段丢失而失败。

### P1-M3：`pi-agent-core` Python 核心

产出：

- `Agent`、`AgentState`、agent loop。
- prompt / continue / abort。
- event subscription。
- tool execution。
- before / after tool hook。
- steer / follow-up queue。
- context transform / convert_to_llm。
- provider cache affinity id（Pi `sessionId`）和 prompt cache retention 透传到 stream function。

完成标准：

- agent 可连续执行多 turn。
- tool call 结果会回灌模型，直到模型停止。
- 工具错误不会中断 runtime，而是作为 tool result 反馈给模型。
- abort 后 session 保留 partial/error 状态。
- agent runtime 产生的 `AgentEvent` 可以直接驱动 P1-M1 TUI。
- 同一 session 多轮调用保持稳定 provider cache affinity id；durable session id 与 provider cache affinity id 的映射，以及 new / fork / clone 后的更新策略明确。
- Negative test：tool error 不破坏后续 turn，steer / follow-up queue 顺序可复现。

### P1-M4：TUI + Agent Runtime Integration

产出：

- TUI 接入真实 `pi-ai` / `pi-agent-core` event source。
- prompt 输入进入 `Agent.prompt()`。
- cancel 连接 `Agent.abort()`。
- tool lifecycle 由 runtime event 驱动渲染。
- faux provider 和真实 provider 都能通过 TUI 运行。

完成标准：

- 用户能在 TUI 中完成代码库问答。
- 用户能看到工具执行状态。
- TUI mock playback 和真实 runtime event 共用同一套渲染路径。

### P1-M5：Coding Tools 与 Policy

产出：

- read / grep / find / list / bash / edit / write / download 工具。
- `!cmd` / `!!cmd` user bash path。
- `user_bash` extension event。
- NeoMAGI policy enhancement：
  - sudo / destructive command 拦截；
  - path allow/deny policy；
  - timeout；
  - truncation；
  - audit 记录。
- tool renderer。

完成标准：

- 用户可要求下载 URL 文件。
- 用户可运行 `!cmd` 和 `!!cmd`。
- 用户可让 agent 做小范围文件修改。
- 工具行为完整进入 session data。

### P1-M6：Session Manager

产出：

- Postgres-backed session manager。
- NeoMAGI session schema version。
- Pi JSONL export/import。
- Pi JSONL historical version migration / import strategy。
- session resume / fork / tree。
- model_change / thinking_level_change entry。
- label / rename / delete 基础能力。

完成标准：

- 一次 CLI session 可退出后恢复。
- session tree 可导航。
- JSONL 可导出并重新导入。
- durable state 以 Postgres 为主。
- model_change 与 thinking_level_change 作为独立 entry 保存。

### P1-M7：Compaction 与 Branch Summary

产出：

- manual compaction。
- auto compaction。
- context overflow recovery。
- branch summary。
- summary schema。
- token budget / context overflow 处理。

完成标准：

- 长 session 能压缩旧上下文后继续工作。
- provider context-window-overflow 后能自动 compact 并重发。
- compact 后仍 overflow 或可压缩量不足时 fail-fast，不进入 compact / retry 循环。
- tree navigation 能保留离开分支的关键上下文。
- summary 记录可导出和审计。

### P1-M8：Extensions / Skills / Prompt Templates

产出：

- resource loader。
- extension loader。
- extension tool / command / event API。
- extension provider registration。
- extension keybinding / widget / footer / header / status line / overlay API。
- custom tool renderer。
- session before-* hook。
- system prompt replacement / context transform hook。
- skill discovery。
- `/skill:name` namespace。
- prompt template expansion。
- context file discovery。
- reload command。
- user_bash interception example。
- sandbox-bash-extension Python 等价示例作为 stretch 产物。

完成标准：

- extension 可以被加载、禁用和替换。
- extension 注册工具走统一 policy。
- skills 和 prompt templates 能影响 prompt context。
- user_bash extension 可以拦截 `!` / `!!`。
- extension keybinding 不覆盖核心不可让渡键位。

### P1-M9：Settings / Auth / Models

产出：

- global/project settings。
- auth storage。
- API key credential。
- 1 种 OAuth provider。
- 每次 LLM call 前重新解析 key 的 hook。
- model registry。
- custom provider/model。
- model selector。
- thinking level selector。
- prompt cache retention setting。

完成标准：

- 用户可以配置和切换模型。
- credential 不进入 repo。
- custom OpenAI-compatible model 可用。
- prompt cache retention 可配置，并在 provider request 中体现。
- 短期 token 过期时，下一次 LLM call 能重新解析 credential。

### P1-M10：Structured Session Export

产出：

- 本地 session export 命令。
- structured session data schema。
- pi-share-hf 类工具兼容性说明。
- 基础脱敏/过滤策略占位。
- 用 P1 demo 生成的样例 session。

完成标准：

- 一次完整 CLI demo 可导出可复放/可分析的 session 文件。
- session data 足以还原用户输入、模型输出、工具调用、错误、取消、compaction 和分支摘要。

## P1 完成定义

P1 完成时，必须可以用一个本地命令启动 NeoMAGI CLI，并连续完成以下核心 demo：

1. 用户询问当前代码库问题，agent 使用 read/search 工具后给出基于证据的回答。
2. 用户要求 agent 做一个小范围代码或文档修改，agent 使用 edit/write 工具完成。
3. 用户使用 `!cmd` 和 `!!cmd` 执行本地 shell，并验证 `!!` 不进入 LLM context。
4. 用户给出 URL 并要求下载文件，agent 通过受控工具完成下载或给出明确失败原因。
5. 用户执行 `/compact` 后继续追问，agent 能保留关键上下文；context overflow recovery 有 fixture 覆盖。
6. 用户 resume 一个历史 session 并继续对话。
7. 用户 fork 当前 session，新分支可独立演化。
8. 用户通过 `/tree` 切换分支，并生成 branch summary。
9. 用户在 session 中切换模型并 cycle thinking level，UI 与 footer 反映新状态。
10. 用户导出 structured JSONL，包含消息、工具调用、工具结果、model_change、thinking_level_change、模型 usage、compaction、branch summary 和错误信息。

P1 stretch demo：

- 用户加载 sandbox bash extension，agent 调用 extension 工具并展示状态。
- 用户完成非核心 OAuth provider 登录。
- 用户运行 cross-provider handoff 回归 fixture。

如果核心 demo 不能连续完成，P1 不视为完成。

## 与后续阶段的边界

P1 给 P2 Memory 提供：

- 统一 message / tool result / event 数据；
- DB-backed memory tool 接入点；
- context transform 接入点；
- session export 数据源；
- skills/context files 与 memory truth 的边界。

P1 不直接把 session summary、Markdown projection 或 custom entries 写成长期 memory。

P1 给 P3 Gateway 提供：

- 可嵌入 AgentSession / runtime API；
- 可订阅事件流；
- proxy stream 预留；
- session lifecycle；
- abort / steer / follow-up 语义；
- rpc mode 的产品参考。

P1 不直接实现 WebChat、Telegram、Slack 或多租户 gateway。

## 风险

- 完整 Pi CLI 产品体验范围明显大于只做底层 runtime，必须用 milestone 分段验收，避免“一次性重写完整 CLI”失控。
- Pi 原生本地 JSONL/session/settings 语义如果照搬为 truth，会与 NeoMAGI Postgres 数据面冲突；实现时必须把 JSONL 降为 export/import/projection。
- pi-mono 主线高频迭代，产品体验等价 + contract-stable 的选择必须优先于逐 commit 跟随。
- TUI Python 实现成本高：stdin 序列解析、宽字符、IME、undo/kill-ring、长粘贴、终端退出恢复都需要专项测试。
- Python 生态没有完全等价的 inline image 方案；P1 必须允许 Kitty / iTerm / sixel 降级为 placeholder 或文件引用。
- OAuth 短期 token 风险按 per-call credential resolution 处理，详见 R8 / M9。
- provider 兼容矩阵巨大；P1 核心只保证有限 provider，完整 14+ provider 和 cross-provider handoff 作为后续扩展。
- extension 能力很强，如果没有统一 policy，会绕过权限、sandbox、timeout 和 audit。
- session data 如果后补，调试、compaction、structured export 和 replay 价值会丢失；必须从 P1 就纳入验收。

## 后续 Architecture 文档待拆分

roadmap 只定义产品需求和验收目标。以下内容应进入 `design_docs/architecture/`：

- Python package/module layout。
- Pi behavior matrix。
- `pi-ai` message、stream event、provider adapter 详细类型。
- prompt cache contract、provider cache-control strategy、session id 双重语义（durable id × provider cache affinity id）的映射策略。
- usage / cost schema。
- opaque continuation fields。
- provider compatibility matrix。
- `pi-agent-core` state machine 和事件顺序。
- `pi-coding-agent` Python SDK / AgentSession contract。
- Postgres-backed session schema。
- Pi JSONL export/import compatibility schema。
- Pi JSONL session version migration。
- tool registry、permission、sandbox、timeout、truncation、audit contract。
- user_bash `!` / `!!` contract。
- TUI component 和 rendering contract。
- terminal image fallback strategy。
- slash command registry 和优先级规则。
- extension API 全表。
- pi-package out-of-scope / stretch boundary。
- resource loader、skills、prompt templates、context files 的发现和优先级规则。
- compaction / branch summary schema。
- context overflow recovery。
- auth storage / model registry / settings schema。
- OAuth provider scope。
- structured session export schema。
- 与 P2 Postgres memory truth 的 adapter contract。
