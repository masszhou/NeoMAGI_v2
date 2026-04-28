---
doc_id: 019dc5eb-b474-767b-968b-9e9e1c3fe59b
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T20:34:09+02:00
---
# Pi Behavior Matrix (P1-M0)

- Status: accepted
- Date: 2026-04-25
- Baseline: pi-mono `97a38bf6` ([file index](pi_mono_baseline.md), ADR-0011)
- Architecture: `design_docs/architecture/p1_pi_cli_technical_architecture.md`
- Roadmap: `design_docs/roadmap/p1_engine_pi.md` § P1-M0

> 行号引用一律指向 pi-mono `97a38bf6`，路径为 pi-mono 仓库相对路径（`packages/...`）。
> 优先级：**Core** = P1 必交；**Stretch** = P1 可选；**Optional** = P1 后置 / parity backlog。
> 强化项与 NeoMAGI-only 行为单列 §I，避免与"复刻"混淆。

## A. Slash 命令全表

`97a38bf6` 内建命令在 `packages/coding-agent/src/core/slash-commands.ts:17–39` 共 21 条；动态命令通过 extension / prompt template / skill 注册。

| # | 命令 | 优先级 | Pi-mono 来源 | NeoMAGI 行为说明 |
| --- | --- | --- | --- | --- |
| 1 | `/settings` | Core | `slash-commands.ts:18`, `agent-session.ts` settings handler | 打开 settings UI |
| 2 | `/model` | Core | `slash-commands.ts:19`, `agent-session.ts` model selector | 模型选择 overlay |
| 3 | `/scoped-models` | Core | `slash-commands.ts:20` | 配置 `Ctrl+P` 模型循环范围 |
| 4 | `/export` | Core | `slash-commands.ts:21`, `core/export-html/`, `session-manager.ts` JSONL writer | 按扩展名导出 HTML / JSONL |
| 5 | `/import` | Core | `slash-commands.ts:22`, `session-manager.ts` import path | 导入 JSONL 到当前会话 |
| 6 | `/share` | Stretch | `slash-commands.ts:23` | GitHub gist 分享（P1 可推迟） |
| 7 | `/copy` | Core | `slash-commands.ts:24` | 复制最后一条 assistant 消息 |
| 8 | `/name` | Core | `slash-commands.ts:25`, `session-manager.ts` `appendSessionInfo` | 写入 `session_info.name` |
| 9 | `/session` | Core | `slash-commands.ts:26` | 会话统计 |
| 10 | `/changelog` | Optional | `slash-commands.ts:27` | NeoMAGI parity backlog |
| 11 | `/hotkeys` | Core | `slash-commands.ts:28` | 显示键位表 |
| 12 | `/fork` | Core | `slash-commands.ts:29`, `agent-session.ts` `fork` 入口 | 从历史 user 消息分叉 |
| 13 | `/clone` | Core | `slash-commands.ts:30`, `agent-session.ts` `clone` 入口 | 复制当前分支为新会话 |
| 14 | `/tree` | Core | `slash-commands.ts:31`, `agent-session.ts` `navigateTree` | 会话树导航 |
| 15 | `/login` | Core | `slash-commands.ts:32`, `auth-storage.ts` | OAuth login |
| 16 | `/logout` | Core | `slash-commands.ts:33`, `auth-storage.ts` | OAuth logout |
| 17 | `/new` | Core | `slash-commands.ts:34` | 新建会话 |
| 18 | `/compact` | Core | `slash-commands.ts:35`, `compaction/compaction.ts` | 手动 compaction，可附 `customInstructions` |
| 19 | `/resume` | Core | `slash-commands.ts:36` | 选择历史会话 resume |
| 20 | `/reload` | Core | `slash-commands.ts:37`, `resource-loader.ts` | 重载 keybindings/extensions/skills/prompts/themes |
| 21 | `/quit` | Core | `slash-commands.ts:38` | 退出 |

动态命令（不计入 21 条 builtin，但 `getCommands()` 返回时合并显示）：

| 类别 | 命名空间 | Pi-mono 来源 | 说明 |
| --- | --- | --- | --- |
| Extension | `<name>` | `extensions/types.ts:1092 registerCommand`, `agent-session.ts` 命令解析 | 通过 `pi.registerCommand` 注册；优先级低于 builtin |
| Prompt template | `<file_basename>` | `prompt-templates.ts` | Markdown 文件直接展开为 prompt（`$1/$@/${@:N}` 等参数） |
| Skill | `/skill:<name>` | `skills.ts`, `settings.skills.enableSkillCommands` | 独立命名空间；不与 builtin / extension 冲突；`enableSkillCommands` 默认 `true` |

解析优先级：`builtin > extension > prompt template > skill`。Extension 命令在 streaming 期间也可立即执行；skill / prompt template 在 prompt/steer/follow-up 投递前展开；queue 中的消息禁止再次解析为 extension 命令（除非走 `prompt()`）。

## B. Run modes

| Mode | 入口 | 输入协议 | 输出协议 | 测试 harness | 优先级 | Pi-mono 来源 |
| --- | --- | --- | --- | --- | --- | --- |
| Interactive (TUI) | TUI 主进程 | 键盘 / bracketed paste | `AgentSessionEvent` → renderer | M1 mock playback (`tui_playback_format.md`) | Core | `packages/tui/src/tui.ts`, `packages/coding-agent/src/core/agent-session.ts` |
| Print | CLI `--print` 模式 | 单 prompt 字符串 | stdout 文本 + 退出码 | snapshot test | Core | `packages/coding-agent/src/core/sdk.ts`（print path） |
| JSON | CLI `--json` 模式 | 单 prompt | session header + `AgentSessionEvent` JSONL | golden JSONL | Stretch | `packages/coding-agent/docs/json.md` |
| RPC | `--rpc` stdio 服务 | LF-delimited `RpcCommand` JSON | LF-delimited `RpcResponse` / `AgentSessionEvent` JSON | scenario JSONL | Stretch | `packages/coding-agent/src/modes/rpc/rpc-types.ts:1–264`, `rpc-mode.ts`, `docs/rpc.md` |
| Python SDK | `cli.core.AgentSession` 直接调用 | API | event subscription | unit test | Core | `packages/coding-agent/src/core/sdk.ts` |

RPC 同步 / 异步响应规则：

- 同步 (`get_state`、`get_messages`、`get_session_stats`、`get_commands`、`get_available_models` 等) → `response.data` 直接返回最终结果。
- 异步 (`prompt`、`steer`、`follow_up`、`new_session`、`switch_session`、`fork`、`clone`、`compact`、`bash`) → 先 `response.success` 表示已接受；后续运行结果通过 event/message 流推送，不会再发送第二条 command response。

完整 RPC 命令清单（30 条，源自 `rpc-types.ts:RpcCommand` union）：`prompt`、`steer`、`follow_up`、`abort`、`new_session`、`get_state`、`set_model`、`cycle_model`、`get_available_models`、`set_thinking_level`、`cycle_thinking_level`、`set_steering_mode`、`set_follow_up_mode`、`compact`、`set_auto_compaction`、`set_auto_retry`、`abort_retry`、`bash`、`abort_bash`、`get_session_stats`、`export_html`、`switch_session`、`fork`、`clone`、`get_fork_messages`、`get_last_assistant_text`、`set_session_name`、`get_messages`、`get_commands`。

## C. Built-in tools

Pi-mono `97a38bf6` 在 `packages/coding-agent/src/core/tools/index.ts` 把工具分成两个 profile，由 entrypoint 决定注入哪一组到 LLM `Agent.state.tools`。NeoMAGI 完全继承这套划分，不增加任何内建工具。

### C.1 Coding profile（默认；`createCodingTools`）

模型可见的 4 个工具。任何 grep / find / ls / 下载 / 网络读取等需求都通过 `bash` 调系统命令完成（`grep` / `rg` / `find` / `ls` / `curl` / `wget` 等），不是单独的工具。

| Tool | 参数 schema 关键字段 | `details` 关键字段 | Policy 标签 | 优先级 | Pi-mono 来源 |
| --- | --- | --- | --- | --- | --- |
| `read` | `path`、`offset?`、`limit?` | `truncation?`，文本/图片混合内容 | `read`、`fs.read` | Core | `packages/coding-agent/src/core/tools/read.ts` |
| `bash` | `command`、`timeout?` | `truncation?`、`fullOutputPath?`、`exitCode` | `bash`、`exec`、`shell.policy` | Core | `tools/bash.ts` |
| `edit` | `path`、`edits: [{oldText, newText}]` | `unifiedDiff`、`firstChangedLine` | `mutate`、`fs.write`、`mutation_queue` | Core | `tools/edit.ts`, `tools/edit-diff.ts` |
| `write` | `path`、`content` | success 时无 details | `mutate`、`fs.write`、`mutation_queue` | Core | `tools/write.ts`, `tools/file-mutation-queue.ts:1–39` |

### C.2 Read-only profile（opt-in；`createReadOnlyTools`）

只读会话使用。`bash` / `edit` / `write` 不注入；模型只能浏览文件系统。

| Tool | 参数 schema 关键字段 | `details` 关键字段 | Policy 标签 | 优先级 | Pi-mono 来源 |
| --- | --- | --- | --- | --- | --- |
| `read` | （同上） | （同上） | `read`、`fs.read` | Core | `tools/read.ts` |
| `grep` | `pattern`、`path?`、`glob?`、`ignoreCase?`、`literal?`、`context?`、`limit?` | `truncation?`、`matchLimit?`、`lineTruncation?` | `read`、`fs.read` | Core (read-only profile) | `tools/grep.ts` |
| `find` | `pattern`、`path?`、`limit?` | `truncation?`、`resultLimit?` | `read`、`fs.read` | Core (read-only profile) | `tools/find.ts` |
| `ls` | `path?`、`limit?` | `truncation?`、`entryLimit?` | `read`、`fs.read` | Core (read-only profile) | `tools/ls.ts` |

### C.3 通用约束

- 所有 tool 透过 `prepareArguments` → JSON schema 校验 → policy/audit wrapper → `execute`；`prepareArguments` 不绕过 schema。
- 工具结果一律转为 `ToolResultMessage`；`isError=true` 包含 LLM 可读错误。
- `truncation` metadata 同步写入 `agent_tool_executions.truncation`。
- `write` / `edit` 共享 `file-mutation-queue.ts` 串行化，避免并行 batch 内多个改动竞争同一工作树（这是与 `executionMode` 正交的一层并发安全）。
- `bash` 默认非 sudo；`sudo`、破坏性命令、特权路径、长任务依据 policy mode 拒绝或要求确认。**网络抓取（`curl` / `wget` / 包管理器）走同一条 shell policy 通道**，没有独立的网络工具 lane。
- `getActiveTools()` / `setActiveTools()` 可以在 profile 之上做进一步收敛，但不能跨 profile 提权（read-only 会话不能临时启用 `bash` / `write` / `edit`）。

## D. ExtensionAPI surface

逐项核对 `packages/coding-agent/src/core/extensions/types.ts:1040–1259`。Python 命名采用 snake_case，但语义必须 1:1 映射。

#### Async 约定（项目级规则）

权威：ADR-0013（`ExtensionAPI` + `ExtensionCommandContext` 共 8 槽位）+ ADR-0014（`ExtensionUIContext` 5 个 dialog / overlay 槽位）。两者合计 13 个异步槽位即项目当前的完整范围。

Pi 用 TypeScript `Promise<X>` 标注的方法，Python `Protocol` 一律声明为 `async def -> X`，实现层 MUST 用 `async def`。Python 结构化类型不允许同步 `def -> X` 满足 `async def -> X` 的 Protocol slot —— 它们运行时返回值不同（`X` vs `Coroutine[Any, Any, X]`）。

当前划分（按 Pi 上游签名）：

- 同步：`register_*` / `get_*` / `set_*` 大多数 / `send_message` / `send_user_message` / `append_entry` / `set_label` / `compact` / `is_idle` / `abort` / `has_pending_messages` / `shutdown` / `get_context_usage` / `get_system_prompt`；UI 中所有非对话方法（`notify` / `on_terminal_input` / `set_*` / `paste_to_editor` / `get_editor_text` / `theme` 系列 / `get_tools_expanded` / `set_tools_expanded`）。
- 异步：
  - **ExtensionAPI**：`exec` / `set_model`。
  - **ExtensionCommandContext**：`wait_for_idle` / `new_session` / `fork` / `navigate_tree` / `switch_session` / `reload`。
  - **ExtensionUIContext**（5 个 dialog-style 方法）：`select` / `confirm` / `input` / `custom` / `editor` —— 全部等待用户响应才 resolve。

### D.1 ExtensionAPI methods（每行均对照 pi-mono 类型签名）

| # | Pi API | Python mirror | Pi-mono 行 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | `on(event, handler)` | `on(event, handler)` | 1040–1077 | 6 类 25+ 重载，见 §E |
| 2 | `registerTool(tool)` | `register_tool(tool)` | 1083–1086 | 走 policy/audit wrapper |
| 3 | `registerCommand(name, options)` | `register_command(name, options)` | 1092 | builtin 优先 |
| 4 | `registerShortcut(keyId, opts)` | `register_shortcut(key_id, opts)` | 1095–1102 | `description?`、`handler` |
| 5 | `registerFlag(name, opts)` | `register_flag(name, opts)` | 1104–1112 | `type: "boolean" \| "string"`、`default?` |
| 6 | `getFlag(name)` | `get_flag(name)` | 1114 | 返回 `bool \| str \| None` |
| 7 | `registerMessageRenderer(customType, renderer)` | `register_message_renderer(custom_type, renderer)` | 1119–1121 | 仅渲染 `CustomMessageEntry`，与 tool renderer 分离 |
| 8 | `sendMessage(message, options?)` | `send_message(message, options)` | 1126–1130 | `triggerTurn?`、`deliverAs?: "steer" \| "followUp" \| "nextTurn"` |
| 9 | `sendUserMessage(content, opts?)` | `send_user_message(content, deliver_as)` | 1136–1141 | 始终触发 turn；streaming 时通过 `deliverAs` 指定 steer/followUp |
| 10 | `appendEntry(customType, data?)` | `append_entry(custom_type, data)` | 1144 | 持久化但不发给 LLM |
| 11 | `setSessionName(name)` | `set_session_name(name)` | 1151 | session 显示名 |
| 12 | `getSessionName()` | `get_session_name()` | 1154 | 读 `session_info.name` |
| 13 | `setLabel(entryId, label?)` | `set_label(entry_id, label)` | 1157 | bookmark / 清除 |
| 14 | `exec(command, args, options?)` | `exec(command, args, options)` | 1160 | extension shell helper；与 user bash / `bash` tool 分离，仍走 policy/audit |
| 15 | `getActiveTools()` | `get_active_tools()` | 1163 | 当前激活工具名列表 |
| 16 | `getAllTools()` | `get_all_tools()` | 1166 | 含 schema + sourceInfo |
| 17 | `setActiveTools(toolNames)` | `set_active_tools(tool_names)` | 1169 | 激活集合写回 |
| 18 | `getCommands()` | `get_commands()` | 1172 | builtin + extension + prompt + skill 合并 |
| 19 | `setModel(model)` | `set_model(model)` | 1180 | 无 API key 时返回 `False` |
| 20 | `getThinkingLevel()` | `get_thinking_level()` | 1183 | |
| 21 | `setThinkingLevel(level)` | `set_thinking_level(level)` | 1186 | clamp 到模型支持等级 |
| 22 | `registerProvider(name, config)` | `register_provider(name, config)` | 1242 | 接受 `ProviderConfig`（见下） |
| 23 | `unregisterProvider(name)` | `unregister_provider(name)` | 1257 | 还原被覆盖的 builtin 模型 |
| 24 | `events: EventBus` | `events: EventBus` | 1259 | property，不是 method；shared bus（见 D.5） |

`ProviderConfig`（`extensions/types.ts:1265–1297`）字段：`baseUrl?`、`apiKey?`、`api?`、`streamSimple?`、`headers?`、`authHeader?`、`models?`、`oauth?: {name, login, refreshToken, getApiKey, modifyModels?}`。

### D.2 ExtensionContext / ExtensionCommandContext

`extensions/types.ts:286–321`（context）+ `321–351`（command 增量）。Python 用 `typing.Protocol` 镜像。

| 字段/方法 | Python | 备注 |
| --- | --- | --- |
| `ui: ExtensionUIContext` | `ui` | 见 D.3 |
| `hasUI: bool` | `has_ui` | Print / RPC 模式可能为 `False` |
| `cwd: str` | `cwd` | 当前会话目录 |
| `sessionManager` | `session_manager` | readonly 视图 |
| `modelRegistry` | `model_registry` | |
| `model: Model \| None` | `model` | |
| `signal: AbortSignal \| None` | `signal` | |
| `isIdle()` | `is_idle()` | |
| `abort()` | `abort()` | |
| `hasPendingMessages()` | `has_pending_messages()` | |
| `shutdown()` | `shutdown()` | |
| `getContextUsage()` | `get_context_usage()` | 返回 `ContextUsage \| None` |
| `compact(opts?)` | `compact(options)` | `CompactOptions { customInstructions?, onComplete?, onError? }` |
| `getSystemPrompt()` | `get_system_prompt()` | |
| `waitForIdle()` (command-only) | `wait_for_idle()` | extensions/types.ts:336 |
| `newSession()` (command-only) | `new_session()` | |
| `fork(entryId, position)` (command-only) | `fork(entry_id, position)` | |
| `navigateTree(targetId, opts)` (command-only) | `navigate_tree(target_id, opts)` | |
| `switchSession(path)` (command-only) | `switch_session(path)` | |
| `reload()` (command-only) | `reload()` | |

### D.3 UI primitives（22 项）

`extensions/types.ts:88–268`。

"async" 列表示 Python Protocol 是否为 `async def`（对应 Pi `Promise<X>` 返回）。

| Pi UI API | Python | 行 | async | 备注 |
| --- | --- | --- | --- | --- |
| `select(title, options, opts?): Promise<string \| undefined>` | `select(...)` | 124 | ✅ | 选择对话框；等待用户选择 |
| `confirm(title, message, opts?): Promise<boolean>` | `confirm(...)` | 127 | ✅ | 确认对话框 |
| `input(title, placeholder?, opts?): Promise<string \| undefined>` | `input(...)` | 130 | ✅ | 文本输入对话框 |
| `notify(message, type?)` | `notify(...)` | 132 | – | `info \| warning \| error` |
| `onTerminalInput(handler)` | `on_terminal_input(handler)` | 133 | – | raw stdin 监听；返回 unsubscribe |
| `setStatus(key, text?)` | `set_status(key, text)` | 135 | – | 状态栏 |
| `setWorkingMessage(message?)` | `set_working_message(message)` | 138 | – | streaming working text |
| `setWorkingIndicator(options?)` | `set_working_indicator(options)` | 140 | – | 自定义 spinner；`frames: []` 隐藏 |
| `setHiddenThinkingLabel(label?)` | `set_hidden_thinking_label(label)` | 144 | – | 隐藏 thinking 块标签 |
| `setWidget(key, content?, options?)` | `set_widget(...)` | 147 | – | `aboveEditor` / `belowEditor` |
| `setFooter(factory?)` | `set_footer(factory)` | 156 | – | 自定义 footer |
| `setHeader(factory?)` | `set_header(factory)` | 158 | – | startup / header |
| `setTitle(title)` | `set_title(title)` | 160 | – | 终端 / tab 标题 |
| `custom<T>(factory, options?): Promise<T>` | `custom(factory, options)` | 188 | ✅ | 焦点 overlay；factory 可同步可异步，wrapper 都 await |
| `pasteToEditor(text)` | `paste_to_editor(text)` | 200 | – | 含大粘贴折叠 |
| `setEditorText(text)` | `set_editor_text(text)` | 203 | – | 替换编辑器缓冲 |
| `getEditorText()` | `get_editor_text()` | 205 | – | 读编辑器缓冲 |
| `editor(title, prefill?): Promise<string \| undefined>` | `editor(title, prefill)` | 207 | ✅ | 多行 editor 对话框 |
| `setEditorComponent(factory?)` | `set_editor_component(factory)` | 215 | – | 替换核心编辑器（vim 模式等） |
| `theme` | `theme` | 230 | – | 当前主题 |
| `getAllThemes()` / `getTheme(name)` | `get_all_themes()` / `get_theme(name)` | 233–235 | – | 主题查询 |
| `setTheme(theme): { success, error? }` | `set_theme(theme) -> dict` | 245 | – | 同步返回状态对象（**不是** void）；调用方读 `success` / `error` |
| `getToolsExpanded()` / `setToolsExpanded(expanded)` | `get_tools_expanded()` / `set_tools_expanded(expanded)` | 250–254 | – | 工具输出展开状态 |

RPC / print mode 必须给出非交互或远程等价实现，不能静默丢弃。

### D.4 Module-Level Helpers（不是 ExtensionAPI 方法）

| 名称 | Pi-mono 来源 | Python 暴露位置 | 备注 |
| --- | --- | --- | --- |
| `createAssistantMessageEventStream` | `packages/ai/src/utils/event-stream.ts:1–87` | `ai_provider`（不是 `pi.create_assistant_message_event_stream`） | 构造 Pi-compatible assistant stream |
| `defineTool` | `packages/coding-agent/src/core/extensions/types.ts:448–457` | `cli.extensions.define_tool` | 顶层 tool 定义 helper；不是 ExtensionAPI 实例方法 |

### D.5 EventBus（property `events`）

`packages/coding-agent/src/core/event-bus.ts:1–33`。Python `Protocol`：

```python
class EventBus(Protocol):
    def emit(self, channel: str, data: object) -> None: ...
    def on(self, channel: str, handler: Callable[[object], None]) -> Callable[[], None]: ...
```

约束：`on` 必须返回 unsubscribe callback；命名映射后不发明 `subscribe`/`publish` 替代命名（plan §W2 风险点）。

## E. Extension events (6 类，25+ 事件)

`extensions/types.ts:459–836` + `1040–1077` `on(...)` 重载。

### E.1 Resource

| 事件 | Result shape | 行 |
| --- | --- | --- |
| `resources_discover` | `ResourcesDiscoverResult` (`{ extensions?, prompts?, skills?, themes? }`) | 459–476 |

### E.2 Session（含 8 个事件）

| 事件 | Result shape | 行 |
| --- | --- | --- |
| `session_start` | void | 477–485 |
| `session_before_switch` | `{ cancel?: bool }` | 486–492 |
| `session_before_fork` | `{ cancel?: bool, skipConversationRestore?: bool }` | 493–499 |
| `session_before_compact` | `{ cancel?: bool, compaction?: CompactionResult }`（替换 Pi 默认 compaction） | 500–508 |
| `session_compact` | void | 509–515 |
| `session_shutdown` | void | 516–523 |
| `session_before_tree` | `{ cancel?, summary?, customInstructions?, replaceInstructions?, label? }` | 524–545 |
| `session_tree` | void | 546–553 |

### E.3 Agent

| 事件 | Result shape | 行 |
| --- | --- | --- |
| `before_agent_start` | `BeforeAgentStartEventResult { message?, systemPrompt? }`（多 handler `message` 累积；`systemPrompt` 链式覆盖，按 extension load 顺序） | 588–600 |
| `agent_start` | void | 601–605 |
| `agent_end` | void | 606–611 |
| `turn_start` | void | 612–618 |
| `turn_end` | void | 619–626 |
| `message_start` | void | 627–632 |
| `message_update` | void | 633–639 |
| `message_end` | void | 640–646 |
| `context` | `ContextEventResult { messages?: AgentMessage[] }`（仅作用于本次 provider call） | 569–574 |
| `before_provider_request` | void | 575–580 |
| `after_provider_response` | void | 581–587 |

### E.4 Model

| 事件 | Result shape | 行 |
| --- | --- | --- |
| `model_select` | void | `extensions/types.ts` ModelSelectEvent |

### E.5 Tool

| 事件 | Result shape | 行 |
| --- | --- | --- |
| `tool_execution_start` | void | core ↔ session 共有 |
| `tool_execution_update` | void | |
| `tool_execution_end` | void | |
| `tool_call` | `ToolCallEventResult { block?: bool, reason?: str }`（按 `toolName` discriminated union: `bash` / `read` / `edit` / `write` / `grep` / `find` / `ls` / `custom`；`event.input` 可原地变更，后续 handler 看到累计修改，无二次 schema 校验） | 824–826 注释 |
| `tool_result` | `ToolResultEventResult { content?, details?, isError? }` | 826 注释 |

### E.6 User bash & input

| 事件 | Result shape | 备注 |
| --- | --- | --- |
| `user_bash` | `UserBashEventResult { operations?: BashOperations, result?: BashResult }`（`extensions/types.ts:947–952`） | `!cmd` / `!!cmd` 触发；handler 二选一：`operations` 替换 bash 执行后端（SSH / 容器 / 自定义 shell），`result` 直接给一个完整 `BashResult` 跳过执行。早期 M0 写过 `{cancel?, output?}` 是错的；以本行为准。 |
| `input` | `InputEventResult` discriminated by `action`（3 变体）：`{action: "continue"}` / `{action: "transform", text, images?}` / `{action: "handled"}`（`extensions/types.ts:719`） | 用户原始输入流；`continue` 透传、`transform` 替换 text / images、`handled` 跳过 agent 处理 |

### E.7 失败隔离

- factory / load 错误 → diagnostics + 不阻塞 CLI。
- handler 错误 → 通过 extension diagnostics / UI / audit 报告，不可 crash。
- extension tool 视为 untrusted（即使本地安装），仍受 policy 限制。

## F. Settings

来源：`packages/coding-agent/src/core/settings-manager.ts`、`defaults.ts`、architecture §Settings, Auth, Models（line 889–938）。

### F.1 Core (P1 必交)

| 字段 | 默认 | 行为 / 备注 |
| --- | --- | --- |
| `provider` / `model` / `thinkingLevel` | 空（首次启动让用户选择） | `cli.core.AgentSession` 启动时校验 |
| `transport` | `auto` | `sse` \| `websocket` \| `auto` |
| `steeringMode` / `followUpMode` | `all` | `all` \| `one-at-a-time` |
| `theme` | `default` | `tui.theme` 锚点 |
| `compaction.enabled` | `true` | |
| `compaction.reserveTokens` | `16384` | `compaction.ts:121-134 DEFAULT_COMPACTION_SETTINGS` |
| `compaction.keepRecentTokens` | `20000` | 同上 |
| `branchSummary.reserveTokens` | `16384` | `branch-summarization.ts` |
| `branchSummary.skipPrompt` | `false` | |
| `retry.enabled` | `true` | |
| `retry.maxRetries` | `3` | |
| `retry.baseDelayMs` | `2000` | |
| `retry.maxDelayMs` | `60000` | |
| `terminal.showImages` | `true` | inline 终端图片能力开关 |
| `terminal.imageWidthCells` | `80` | 渲染宽度 |
| `terminal.clearOnShrink` | `true` | resize 重绘 |
| `images.autoResize` | `true` | 出站图片缩放到 2000×2000 |
| `images.blockImages` | `false` | 禁止把图片发给 provider |
| `skills.enableSkillCommands` | `true` | `/skill:name` namespace 总开关 |
| `shell.shellPath` | 系统默认 | `bash` tool / extension `exec` 的 shell |
| `shell.shellCommandPrefix` | 空 | wrapper 前缀 |
| `resources.{packages, extensions, skills, prompts, themes}` | 空数组 | resource-loader 优先级条目 |
| `enabledModels` | 空 | Ctrl+P 循环范围 |
| `tui.doubleEscapeAction` | `tree` | `tree` \| `fork` \| `none` |
| `tui.treeFilterMode` | `current_only` | |
| `tui.showHardwareCursor` | `false` | |
| `tui.editorPaddingX` | `0` | |
| `tui.autocompleteMaxVisible` | `8` | |
| markdown 子项（行宽、code theme、wrapping 等） | Pi 默认 | `tui` 模块消费 |
| `sessionDir` | OS 默认 | 仅控制 projection / export / import 路径，不替代 DB |

### F.2 Parity backlog（Optional）

`lastChangelogVersion`、`hideThinkingBlock`、`quietStartup`、`npmCommand`、`collapseChangelog`、`enableInstallTelemetry`、`thinkingBudgets`。Pi-compatible settings importer 必须保留未知字段。

### F.3 Config layering

1. 代码默认 → 2. global settings → 3. project settings → 4. CLI/runtime overrides → 5. session state（model / thinking 还原）。

### F.4 Auth + Model

- Auth：`auth-storage.ts` 文件锁 + 权限收紧；`{type:"api_key" \| "oauth", ...}`；导出 session 不写回 secret。
- Model registry：builtin (`packages/ai/src/models.ts`) + `model-registry.ts` 配置覆盖 + extension `registerProvider`。
- Provider 覆盖字段：`baseUrl`、`api`、`apiKey`、`headers`、`compat`、`authHeader`。

## G. Session entries (9 类)

来自 `packages/coding-agent/src/core/session-manager.ts` + architecture line 509–519。`SessionHeader.timestamp` / `SessionEntryBase.timestamp` 是 ISO8601；message timestamp 是 Unix ms（见 ADR-0010）。

| Entry `type` | 关键字段 | 是否参与 context | Pi-mono 来源 |
| --- | --- | --- | --- |
| `session` (header) | `version=3`, `id`, `timestamp`(ISO8601), `cwd`, `parentSession?` | header | `session-manager.ts` SessionHeader |
| `message` | `message: AgentMessage` | yes | `session-manager.ts` MessageEntry |
| `thinking_level_change` | `thinkingLevel` | state only | `session-manager.ts` |
| `model_change` | `provider`, `modelId` | state only | `session-manager.ts` |
| `compaction` | `summary`, `firstKeptEntryId`, `tokensBefore`, `details?`, `fromHook?` | yes（注入为 `compactionSummary` role） | `session-manager.ts` + `compaction/compaction.ts` |
| `branch_summary` | `fromId`, `summary`, `details?`, `fromHook?` | yes（注入为 `branchSummary` role） | `compaction/branch-summarization.ts` |
| `custom` | `customType`, `data?` | no | `session-manager.ts` |
| `custom_message` | `customType`, `content`, `display`, `details?` | yes（注入为 `custom` role） | `session-manager.ts` + `messages.ts` `createCustomMessage` |
| `label` | `targetId`, `label?` | no | `session-manager.ts` |
| `session_info` | `name?` | no | `session-manager.ts` |

四类 coding 自定义 message role（`packages/coding-agent/src/core/messages.ts:29–80`）：`bashExecution`（含 `excludeFromContext`）、`custom`、`branchSummary`、`compactionSummary`。这些不在 `agent_core` core 层声明，仅在 `cli.core` 通过 declaration-merging 等价方式扩展 union（W2 实现要求）。

`build_session_context(leaf)` 从 root 走到 leaf，派生 messages、model、thinking level、compaction 边界、branch summary、custom message。`CURRENT_SESSION_VERSION = 3`。

## H. Compaction & branch summary

来源：`packages/coding-agent/src/core/compaction/compaction.ts:33–839`，`branch-summarization.ts`，`utils.ts`，architecture §Compaction and Branch Summary（line 979–1024）。

### H.1 默认值

```python
DEFAULT_COMPACTION_SETTINGS = {
    "enabled": True,
    "reserveTokens": 16384,
    "keepRecentTokens": 20000,
}
```

`compaction.ts:121–134`。

### H.2 触发判定（auto）

`shouldCompact(contextTokens, contextWindow, settings)`：当 `contextTokens > contextWindow - reserveTokens` 时触发。`contextTokens` 由 `calculateContextTokens(usage) = usage.input + usage.cacheRead + usage.cacheWrite + usage.output`（`compaction.ts:135–155`）计算。

### H.3 Cut point 规则

- 寻找用户、assistant、bashExecution、custom/branch-summary 处的可切点；**严禁切在孤立 toolResult**。
- 保留最近 `keepRecentTokens` 预算内消息；保护 `firstKeptEntryId`。
- 重复 compaction 时把上次 summary + 留存 messages 拼回。
- 单 turn 超出预算时支持 split-turn compaction（`compaction.ts:596–839`）。
- 写入 `compaction` entry：`summary`、`firstKeptEntryId`、`tokensBefore`、`details`、`fromHook`。

### H.4 Overflow 双路径

| 路径 | 判定 | 来源 |
| --- | --- | --- |
| 显式错误 | provider error message 命中 `OVERFLOW_PATTERNS` 且不命中 `NON_OVERFLOW_PATTERNS` | `packages/ai/src/utils/overflow.ts` |
| Silent | `assistantMessage.stopReason == "stop"` 且 `usage.input + usage.cacheRead > model.contextWindow` | `agent-loop.ts` overflow recovery |

恢复逻辑：第一次检测 → compaction + 一次重试。重试仍 overflow → fail-fast。下一次成功的非-overflow 调用重置预算；同一 session 可多次进入 overflow recovery，但每次必须由一次成功 call 隔开。

### H.5 Branch summary

- 切换 tree 时找到旧 leaf 与目标的最深公共祖先。
- 总结被离开的 entries → `branch_summary` entry（`fromId`）。
- `details` 默认累积 `readFiles` + `modifiedFiles`（来自历史 `read` / `edit` / `write` 工具调用 args；NeoMAGI policy/audit wrapper 必须保留 args）。
- Extension 可通过 `session_before_tree` 提供 `summary` / `details` / `customInstructions` / `label` / `replaceInstructions`。

### H.6 Summary 文本格式

```markdown
## Goal
## Constraints & Preferences
## Progress
### Done
### In Progress
### Blocked
## Key Decisions
## Next Steps
## Critical Context
<read-files>...</read-files>
<modified-files>...</modified-files>
```

包装常量：`COMPACTION_SUMMARY_PREFIX/SUFFIX`、`BRANCH_SUMMARY_PREFIX/SUFFIX`（`messages.ts:11–24`）。

`<read-files>` / `<modified-files>` 由历史 `read` / `edit` / `write` 工具 args 抽取；NeoMAGI policy/audit wrapper 必须保留这些 args，否则 compaction 丢上下文。

Compaction 与 branch summary 都不是长期记忆真理。P2 可消费它们作为 session context 或 candidate evidence，但 memory 写入仍需 DB 后端 memory tool 审批。

## I. NeoMAGI 强化项（相对 Pi 的强化，非复刻）

| 项 | 说明 | 来源 |
| --- | --- | --- |
| Postgres 真理 | `agent_sessions / agent_session_entries / agent_messages / agent_tool_executions / agent_audit_events`（architecture line 530–627）；DB 优先写入，JSONL 仅做 import / export / projection | ADR-0008、architecture §NeoMAGI Postgres Schema |
| Policy contract | `PolicyRequest` / `PolicyDecision`，`effect` ∈ `{allow, block, confirm}`；`confirm` 经 TUI/RPC/SDK UI adapter 解析；扩展工具走同一 wrapper | architecture §Policy Contract |
| Audit | 所有 shell / file / network / memory / task mutation 必经 `agent_audit_events`；secret 不入 audit | ADR-0007、architecture §Tool Registry |
| Sandbox | `bash` / `download` / `exec` 通过 sandbox adapter 执行（local subprocess 起步，未来 container/SSH） | architecture §Open Design Questions |
| File mutation queue | NeoMAGI 仍复刻 Pi 的 `file-mutation-queue.ts`，但与 policy/audit wrapper 集成 | `tools/file-mutation-queue.ts:1–39` |
| Cache affinity 双 ID | NeoMAGI 区分 durable Postgres `session_id` 与 provider `cache_affinity_id`（fork/clone/new 显式定义是否复用） | architecture §Model and Provider |
| Fail-fast DB | DB 不可达时主路径硬错（不再静默退化到 JSONL） | ADR-0007 |
| **Anti-feature：禁止内建 `download` 工具** | NeoMAGI 不提供独立的 `download` / 网络抓取工具。下载需求一律走 `bash`（`curl` / `wget` / 包管理器），让所有外部字节都受 shell policy 这一条通道治理；新增网络工具会复制 mutation 表面，破坏 policy 单一边界。需要新增此类工具必须先开 ADR。 | 项目理念；与 ADR-0007 fail-fast / ADR-0008 memory truth 一脉 |
| Memory truth = Postgres | 摘要 / custom message / skill / context file 不自动转记忆；memory 写入需 DB 后端 memory tool 审批 | ADR-0008、architecture §P2 Memory Adapter Boundary |

这些项不在 Pi `97a38bf6` 中存在或不存在统一实现；fixture 与 behavior matrix 中标注 `NeoMAGI-only`，与"复刻项"严格区分。
