---
doc_id: 019dc5e8-4476-7355-9f38-1379447fbad3
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T20:30:21+02:00
---
# Pi-mono Baseline File Index (commit `97a38bf6`)

- Status: accepted
- Date: 2026-04-25
- Authority: `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md` (ADR-0011)
- Cross-references: `design_docs/architecture/pi_behavior_matrix.md` (W1)
- Plan: `dev_docs/plans/p1_m0_pi_baseline_and_fixtures.md` § W5

## 1. 基线声明

- 上游仓库：`badlogic/pi-mono` (`https://github.com/badlogic/pi-mono`)。
- 锁定 commit：`97a38bf65217d89619b3386c620333a97ee391b7`（短形 `97a38bf6`）。
- 锁定 tree URL：`https://github.com/badlogic/pi-mono/tree/97a38bf65217d89619b3386c620333a97ee391b7`。
- Fetch 时间（local）：2026-04-25。
- 升级流程：必须先新增（或修订）ADR，提交 baseline diff review；禁止在普通实现 PR 内静默 bump。详见 ADR-0011 § 影响。
- 所有路径均为 pi-mono 仓库内相对路径（`packages/...`），不是本仓库路径。开发者本地 clone 位置由各自工作站决定，不入库。

## 2. 关键文件索引

行号区间均参照 `97a38bf6`；后续若上游改动，行号失效但路径仍保留为锚点。匹配 entry 由 `pi_behavior_matrix.md` 的 A–I 区块反向引用。

### 2.1 `packages/ai/` —— `ai_provider` 协议来源

| 文件 | 关键节段（行） | 用途 | Matrix 锚点 |
| --- | --- | --- | --- |
| `packages/ai/src/types.ts` | 5–43 (`KnownApi` / `KnownProvider`) | API / Provider 枚举 | F、Source Map |
| | 45–58 (`ThinkingLevel`、`ThinkingBudgets`、`CacheRetention`、`Transport`) | thinking / cache 枚举 | F、H |
| | 60–135 (`ProviderResponse` / `StreamOptions` / `SimpleStreamOptions` / `StreamFunction`) | provider stream 入参 | F |
| | 141–175 (`TextSignatureV1`、`TextContent`、`ThinkingContent`、`ImageContent`、`ToolCall`) | content blocks | A 引用、Architecture §Content Blocks |
| | 177–192 (`Usage` / `StopReason`) | usage + 停因 | F、H |
| | 194–223 (`UserMessage` / `AssistantMessage` / `ToolResultMessage`) | 三类 message | G、H |
| | 227–245 (`Tool` / `Context`) | provider tool schema | C |
| | 247–263 (`AssistantMessageEvent`) | 12 帧 stream union | E、Architecture §Assistant Stream |
| | 265–391 (`OpenAICompletionsCompat`、`OpenAIResponsesCompat`、`OpenRouterRouting`、`VercelGatewayRouting`) | provider compat 字段 | F backlog |
| | 393–416 (`Model<TApi>`) | model registry record（含 `contextWindow`） | F |
| `packages/ai/src/stream.ts` | 1–end | `stream` / `streamSimple` 入口 | E |
| `packages/ai/src/utils/event-stream.ts` | 1–87 (`createAssistantMessageEventStream`) | 模块级 helper（非 `ExtensionAPI` 方法） | D Module-Level Helpers |
| `packages/ai/src/utils/overflow.ts` | 1–138 (`OVERFLOW_PATTERNS` / `NON_OVERFLOW_PATTERNS` / `isContextOverflow`) | overflow 双路径 | H |
| `packages/ai/src/providers/anthropic.ts` | cache_control 段、overflow message 段 | Anthropic cache + overflow | H、F |
| `packages/ai/src/providers/openai-responses.ts` | `prompt_cache_key` / `cache_retention` 段 | OpenAI Responses cache + retention | F、H |
| `packages/ai/src/providers/openai-completions.ts` | `prompt_cache_key` + Anthropic-style `cache_control` 段 | OpenAI Chat Completions + compat cache | F、H |
| `packages/ai/src/providers/amazon-bedrock.ts` | `cachePoint` 段 | Bedrock Converse cache | F、H |
| `packages/ai/src/providers/faux.ts` | sessionId 模拟 + common-prefix 计费 | faux provider 用于 fixture | E、F |
| `packages/ai/src/models.ts` | model 注册表 | Model registry 内建集 | F |
| `packages/ai/src/api-registry.ts` | API ↔ stream 映射 | API family 注册 | F |

### 2.2 `packages/agent/` —— `agent_core` 协议来源

| 文件 | 关键节段（行） | 用途 | Matrix 锚点 |
| --- | --- | --- | --- |
| `packages/agent/src/types.ts` | 24–34 (`StreamFn`) | provider 注入点 | E |
| | 36–96 (`ToolExecutionMode`、`AgentToolCall`、`BeforeToolCallResult`、`AfterToolCallResult`、`BeforeToolCallContext`、`AfterToolCallContext`) | tool 钩子契约 | E |
| | 97–222 (`AgentLoopConfig`) | loop 配置（含 retry / overflow / steering / follow-up） | E、H |
| | 223–254 (`ThinkingLevel`、`CustomAgentMessages`、`AgentMessage`) | core 层 union 与 declaration merging | E、Architecture §Agent State |
| | 256–290 (`AgentState`、`AgentToolResult`) | 状态与工具返回 | E |
| | 295–335 (`AgentTool`、`AgentContext`) | agent 内部 tool 形态 | C、E |
| | 337–352 (`AgentEvent`) | core 10 帧 union | E、Architecture §Agent Events |
| `packages/agent/src/agent.ts` | 94–157 (`AgentOptions`) | Agent 构造选项 | E |
| | 158–end (`Agent` class) | `prompt`/`continue`/`abort`/`steer`/`follow_up`/`subscribe` 实现 | E |
| `packages/agent/src/agent-loop.ts` | 25–63 (`AgentEventSink`、`agentLoop`) | loop 入口 | E |
| | 64–end (`agentLoopContinue`) | 续跑路径 + 串/并发 tool 执行 | E |

### 2.3 `packages/coding-agent/` —— `cli.core` / `cli.tools` / `cli.extensions` 来源

| 文件 | 关键节段（行） | 用途 | Matrix 锚点 |
| --- | --- | --- | --- |
| `packages/coding-agent/src/core/agent-session.ts` | 全文（~3082 行）| AgentSession 主体 | A、B、E、G、H |
| `packages/coding-agent/src/core/agent-session-runtime.ts` | 全文 | runtime 适配 | B、E |
| `packages/coding-agent/src/core/agent-session-services.ts` | 全文 | 服务装配 | B、E |
| `packages/coding-agent/src/core/sdk.ts` | 全文 | Python SDK 等价入口 | B |
| `packages/coding-agent/src/core/session-manager.ts` | 全文（~1425 行） | 9 类 entry + tree + JSONL projection | G、H |
| `packages/coding-agent/src/core/messages.ts` | 11–24 (`COMPACTION_SUMMARY_PREFIX/SUFFIX`、`BRANCH_SUMMARY_PREFIX/SUFFIX`) | summary 包装常量 | H |
| | 29–80 (`BashExecutionMessage`、`CustomMessage`、`BranchSummaryMessage`、`CompactionSummaryMessage`) | 4 个自定义 role | G |
| | 82–195 (`bashExecutionToText`、`createBranchSummaryMessage`、`createCompactionSummaryMessage`、`createCustomMessage`、`convertToLlm`) | role 转换与 LLM 边界 | E、G |
| `packages/coding-agent/src/core/slash-commands.ts` | 1–39 | builtin command 注册 | A |
| `packages/coding-agent/src/core/settings-manager.ts` | 全文 | settings schema + 默认值 | F |
| `packages/coding-agent/src/core/auth-storage.ts` | 全文 | auth.json + lock | F |
| `packages/coding-agent/src/core/model-registry.ts` | 全文 | builtin + override + extension provider | F |
| `packages/coding-agent/src/core/model-resolver.ts` | 全文 | model id ↔ provider 解析 | F |
| `packages/coding-agent/src/core/resource-loader.ts` | 全文 | 资源发现 + 优先级 | F、Architecture §Resource Loader |
| `packages/coding-agent/src/core/prompt-templates.ts` | 全文 | `$1/$@/${@:N}` | A |
| `packages/coding-agent/src/core/skills.ts` | 全文 | `/skill:` 与 `enableSkillCommands` | A、F |
| `packages/coding-agent/src/core/system-prompt.ts` | 全文 | `BuildSystemPromptOptions` | E |
| `packages/coding-agent/src/core/exec.ts` | 全文 | extension `exec` helper（policy 边界外的本地 helper） | D |
| `packages/coding-agent/src/core/event-bus.ts` | 1–33 (`EventBus`、`emit` / `on`) | extension shared bus | D |
| `packages/coding-agent/src/core/keybindings.ts` | 全文 | shortcut 注册 | F |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 33–101 (`CompactionDetails`) | details schema | H |
| | 103–134 (`CompactionResult` / `CompactionSettings` / `DEFAULT_COMPACTION_SETTINGS`) | 默认值 (`reserveTokens=16384`、`keepRecentTokens=20000`) | H |
| | 135–230 (`calculateContextTokens`、`getLastAssistantUsage`、`estimateContextTokens`、`shouldCompact`、`estimateTokens`) | 触发判定 | H |
| | 344–595 (`findTurnStartIndex`、`findCutPoint`、`prepareCompaction`) | cut point + preparation | H |
| | 596–839 (`prepareCompaction` 续) | split-turn 处理 | H |
| `packages/coding-agent/src/core/compaction/branch-summarization.ts` | 全文 | branch summary 生成 | H |
| `packages/coding-agent/src/core/compaction/utils.ts` | 全文 | 共享工具 | H |
| `packages/coding-agent/src/core/extensions/types.ts` | 78–87 (re-export) | 类型再导出 | D |
| | 88–268 (`ExtensionUIDialogOptions`、`WidgetPlacement`、`ExtensionWidgetOptions`、`TerminalInputHandler`、`WorkingIndicatorOptions`、`ExtensionUIContext`) | 22 项 UI primitive | D UI |
| | 269–321 (`ContextUsage`、`CompactOptions`、`ExtensionContext`) | extension 上下文 | D |
| | 321–351 (`ExtensionCommandContext`) | command 上下文增量 | D |
| | 352–447 (`ToolRenderResultOptions`、`ToolRenderContext`、`ToolDefinition`) | 工具定义 + render | C |
| | 448–457 (`defineTool`) | 模块级 helper | D Module-Level Helpers |
| | 459–836 (各 ExtensionEvent payload + `Result` shapes) | 6 类 25+ 事件 | E |
| | 1040–1108 (`on(...)` 重载列表) | 事件订阅完整签名 | E |
| | 1083–1259 (`registerTool`/`registerCommand`/`registerShortcut`/`registerFlag`/`getFlag`/`registerMessageRenderer`/`sendMessage`/`sendUserMessage`/`appendEntry`/`setSessionName`/`getSessionName`/`setLabel`/`exec`/`getActiveTools`/`getAllTools`/`setActiveTools`/`getCommands`/`setModel`/`getThinkingLevel`/`setThinkingLevel`/`registerProvider`/`unregisterProvider`/`events`) | ExtensionAPI 全表 | D |
| | 1260–1500 (`ProviderConfig`、`ProviderModelConfig`、OAuth 子结构) | 自定义 provider 注册 schema | D |
| `packages/coding-agent/src/core/extensions/index.ts` | 全文 | extension 注册入口 | Source Map |
| `packages/coding-agent/src/core/extensions/loader.ts` | 全文 | 加载器 | Source Map |
| `packages/coding-agent/src/core/extensions/runner.ts` | 全文 | runner | Source Map |
| `packages/coding-agent/src/core/extensions/wrapper.ts` | 全文 | policy/audit wrapper | C、D |
| `packages/coding-agent/src/core/tools/index.ts` | 全文 | builtin 工具注册表 | C |
| `packages/coding-agent/src/core/tools/read.ts` | 全文 | `read` 工具 | C |
| `packages/coding-agent/src/core/tools/grep.ts` | 全文 | `grep` 工具 | C |
| `packages/coding-agent/src/core/tools/find.ts` | 全文 | `find` 工具 | C |
| `packages/coding-agent/src/core/tools/ls.ts` | 全文 | `ls` 工具 | C |
| `packages/coding-agent/src/core/tools/write.ts` | 全文 | `write` 工具（mutation queue 用户） | C |
| `packages/coding-agent/src/core/tools/edit.ts` | 全文 | `edit` 工具（mutation queue 用户） | C |
| `packages/coding-agent/src/core/tools/edit-diff.ts` | 全文 | edit unified diff | C |
| `packages/coding-agent/src/core/tools/bash.ts` | 全文 | `bash` 工具 + 策略 | C |
| `packages/coding-agent/src/core/tools/file-mutation-queue.ts` | 1–39 | 文件改动串行化队列 | C |
| `packages/coding-agent/src/core/tools/path-utils.ts` | 全文 | 路径工具 | C |
| `packages/coding-agent/src/core/tools/render-utils.ts` | 全文 | 渲染工具 | C |
| `packages/coding-agent/src/core/tools/tool-definition-wrapper.ts` | 全文 | builtin → AgentTool wrapper | C |
| `packages/coding-agent/src/core/tools/truncate.ts` | 全文 | output 截断 | C |
| `packages/coding-agent/src/modes/rpc/rpc-types.ts` | 1–264 | RPC command / response / event 类型 | B |
| `packages/coding-agent/src/modes/rpc/rpc-mode.ts` | 全文 | RPC server 主循环 | B |
| `packages/coding-agent/src/modes/rpc/rpc-client.ts` | 全文 | RPC client helper | B |
| `packages/coding-agent/src/modes/rpc/jsonl.ts` | 全文 | JSONL 帧切割 | B |
| `packages/coding-agent/docs/extensions.md` | 全文 | extension 协议说明 | D |
| `packages/coding-agent/docs/session.md` | 全文 | session JSONL 协议 | G |
| `packages/coding-agent/docs/compaction.md` | 全文 | compaction 协议 | H |

### 2.4 `packages/tui/` —— TUI 来源

| 文件 | 用途 | Matrix 锚点 |
| --- | --- | --- |
| `packages/tui/src/tui.ts` | 终端生命周期、raw mode、bracketed paste、resize | TUI Contract |
| `packages/tui/src/terminal.ts` | 终端能力探测 | TUI Contract |
| `packages/tui/src/editor-component.ts` | 多行 editor + 历史 | TUI Contract |
| `packages/tui/src/autocomplete.ts` / `fuzzy.ts` | `/` `@` 补全 | TUI Contract、A |
| `packages/tui/src/keybindings.ts` / `keys.ts` | 键位映射 | F |
| `packages/tui/src/components/` | overlay / select / settings / loaders | TUI Contract |
| `packages/tui/src/terminal-image.ts` | 终端内图片 | F |
| `packages/tui/src/stdin-buffer.ts` / `kill-ring.ts` / `undo-stack.ts` | 输入缓冲与历史 | TUI Contract |

## 3. 升级与漂移政策

- **不静默升级**：开发期间发现 upstream 有修复或新行为时，按 ADR-0011 默认入 backlog，并在 `dev_docs/logs/p1_m0_closeout.md` 的 “Upstream observed but deferred” 段记录。
- **行号失效不更新基线**：本索引的行号面向 `97a38bf6`；如果发现 upstream 行号已变，必须在升级 ADR 中重写本文件。
- **本仓库局部修复偏离**：如果 NeoMAGI 选择修正 `97a38bf6` 中的缺陷，必须在对应代码或 fixture 处显式注释偏离原因，并在 closeout 段做最小记录。

## 4. 回查脚本

在任意本地 pi-mono clone 中校验基线 commit（路径由开发者自定）：

```sh
# 验证基线 commit
git -C "$PI_MONO_DIR" rev-parse HEAD
# expected: 97a38bf65217d89619b3386c620333a97ee391b7
```

或直接通过 GitHub API 校验，不需要本地 clone：

```sh
curl -fsSL https://api.github.com/repos/badlogic/pi-mono/commits/97a38bf65217d89619b3386c620333a97ee391b7 \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['sha'])"
# expected: 97a38bf65217d89619b3386c620333a97ee391b7
```
