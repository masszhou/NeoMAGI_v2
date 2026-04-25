---
doc_id: 019dc588-51c0-7380-bfce-c1261fd1b5e9
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T18:45:28+02:00
---
# P1-M0 Implementation Plan: Pi Baseline & Fixture Scaffolding

- Status: accepted
- Date: 2026-04-25
- Roadmap: `design_docs/roadmap/p1_engine_pi.md` (§ P1-M0)
- Architecture: `design_docs/architecture/p1_pi_cli_technical_architecture.md`
- Governing decisions:
  - ADR-0009 Pi CLI product equivalence contract — `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
  - ADR-0010 Use pydantic v2 for protocol types — `design_docs/decisions/0010-use-pydantic-v2-for-protocol-types.md`
  - ADR-0011 Freeze pi-mono baseline at `97a38bf6` — `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md`
- Reference baseline: pi-mono `main@97a38bf6` (fetch 2026-04-25); 见 ADR-0011 锁定与升级流程

## 目标

落实 P1-M0：固定 pi-mono 协议基线，产出 behavior matrix，建立 Python 类型契约与 26 条兼容性 fixture，让 M1（TUI skeleton）、M2（pi-ai 核心）、M3（agent runtime）可以从同一份 contract 起步。

M0 不做 runtime 实现；它只产出**类型 + 数据 + 文档**。runtime 在 M1–M3。

## 范围

In scope:

- 按 ADR-0011 落实 pi-mono `97a38bf6` 基线的文件级路径与行号索引。
- 产出完整 Pi CLI behavior matrix。
- 建立 Python 包骨架（`ai_provider` / `agent_core` / `cli.core` / `cli.tools` / `cli.extensions` / `tui` / `storage` / `policy`）。
- 按 ADR-0010 用 pydantic v2 实现 Pi 协议类型（content block / message / event / session entry / extension types）。
- 复刻 `OVERFLOW_PATTERNS` / `NON_OVERFLOW_PATTERNS` 与 usage 归一化常量。
- 在 `tests/fixtures/pi_compat/` 建 26 条兼容性 fixture 目录骨架，并交付至少 8 条核心 fixture 含 `input` + `expected`。
- 定义 TUI mock playback 的文件协议（M1 消费）。

Out of scope（属于 M1+）:

- TUI 渲染、editor、overlay、差分输出。
- faux/真实 provider 实现。
- Agent loop、tool execution、steer/follow-up queue。
- Postgres schema 上线（M6）。
- extension loader / policy 执行链路。

## 工作分解

| ID | 工作项 | 产出 |
| --- | --- | --- |
| W0 | 包骨架 + lint gate | `src/<pkg>/__init__.py` + `pyproject.toml` 注册 + `just lint` 通过 |
| W1 | Behavior matrix | `design_docs/architecture/pi_behavior_matrix.md` |
| W2 | 协议类型声明 | `src/ai_provider/types.py`、`src/agent_core/types.py`、`src/cli/core/session_types.py`、`src/cli/extensions/types.py` |
| W3 | Overflow + usage 常量 | `src/ai_provider/overflow.py`、`src/ai_provider/usage.py` + 单测 |
| W4 | 26 条 compatibility fixture | `tests/fixtures/pi_compat/<scene>/` |
| W5 | Pi 基线文件索引（实现 ADR-0011） | `dev_docs/plans/p1_m0/pi_mono_baseline.md` |
| W6 | TUI mock playback 协议 | `design_docs/architecture/tui_playback_format.md` |
| W7 | 进度归档 | `dev_docs/progress/progress.md` 追加 + `dev_docs/plans/p1_m0/closeout.md` |

### W0. 包骨架

**Build backend 选型**：W0 显式选用 **hatchling** 作为 build backend，与 ADR-0002 选定的 uv 默认行为一致。当前 `pyproject.toml` 没有 `[build-system]`，靠 uv editable fallback 工作；W0 必须把它补上，否则 `uv sync` + 顶级包 import 验收会随 uv 行为漂移。

新增目录与 `__init__.py`：

```
src/
  ai_provider/__init__.py
  agent_core/__init__.py
  cli/__init__.py
  cli/core/__init__.py
  cli/tools/__init__.py
  cli/extensions/__init__.py
  tui/__init__.py
  storage/__init__.py
  policy/__init__.py
  infra/__init__.py
```

每个 `__init__.py` 顶部 docstring 必须引用 architecture 章节 + pi-mono 文件路径，便于回查。

**现有目录处理**（在 W0 内一次性完成，避免后续 import 路径再变）：

- `src/infra/` 已存在但没有 `__init__.py`（当前以 PEP 420 namespace + `python -m src.infra.complexity_guard` 形式运行）。W0 增加 `src/infra/__init__.py`，把它正式作为 hatchling 顶级包，并更新 `justfile`：
  - `python -m src.infra.complexity_guard check` → `python -m infra.complexity_guard check`
  - `python -m src.infra.complexity_guard report` → `python -m infra.complexity_guard report`
  - `python -m src.infra.complexity_guard write-baseline` → `python -m infra.complexity_guard write-baseline`
- `src/tui/` 当前是空目录，W0 加上 `src/tui/__init__.py` 即可正式启用，无需删建。

**`pyproject.toml`** 显式增加：

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = [
  "src/ai_provider",
  "src/agent_core",
  "src/cli",
  "src/tui",
  "src/storage",
  "src/policy",
  "src/infra",
]
```

- 维持 `requires-python = ">=3.14"`。
- 生产依赖：`pydantic>=2,<3`（ADR-0010）。
- 开发依赖：`pytest`、`hypothesis`（fixture round-trip 用）。

`just lint` 必须 green（justfile 改动后），`complexity_guard` baseline 不退化。

### W1. Behavior Matrix

新文件：`design_docs/architecture/pi_behavior_matrix.md`

章节：

- **A. Slash 命令全表**：25 条内建 + dynamic（`/skill:` / prompt template / extension），每条标注 P1 Core / Stretch / Optional 与 pi-mono 源文件行号。
- **B. Run modes**：interactive / print / json / rpc / SDK，列出每个 mode 入口、输出协议、测试 harness。
- **C. Built-in tools**：8 条（read/grep/find/ls/write/edit/bash + neomagi `download`），含参数 schema、details schema、policy 标签。
- **D. ExtensionAPI surface**：完整列出 architecture §`ExtensionAPI Surface` 表格中的每一项 method 与 property，**逐行核对**，不靠行号区间引用。必须显式覆盖：`on` / `registerTool` / `registerCommand` / `registerShortcut` / `registerFlag` / `getFlag` / `registerMessageRenderer` / `sendMessage` / `sendUserMessage` / `appendEntry` / `setSessionName` / `getSessionName` / `setLabel` / `exec` / `getActiveTools` / `setActiveTools` / `getAllTools` / `getCommands` / `setModel` / `getThinkingLevel` / `setThinkingLevel` / **`registerProvider` / `unregisterProvider` / `events: EventBus`**。再加 22 项 ExtensionUIContext primitive、§`Module-Level Helpers` 的 `createAssistantMessageEventStream` / `defineTool`。
- **E. Extension events**：6 类 × 25+ 事件名，含 result shape。
- **F. Settings**：分组列出 Core P1 必须支持的字段（compaction / retry / images / shell / skills / TUI 等）+ parity backlog。
- **G. Session entries**：9 类 entry 表（与 architecture line 509–519 对齐）。
- **H. Compaction & branch summary**：默认值（reserveTokens=16384、keepRecentTokens=20000）+ overflow 双路径。
- **I. NeoMAGI 强化项**：policy / audit / postgres truth / sandbox / file-mutation-queue —— 明确"这是相对 pi 的强化"，避免混入"复刻项"。

每行末附 `pi-mono` 文件路径（含 line range，便于 W5 的基线锁交叉引用）。

### W2. 协议类型声明

类型方案已由 ADR-0010 锁定为 **pydantic v2**。W2 不做选型试样，直接按 ADR-0010 的实施约束写模型。

**ADR-0010 实施约束**（W2 必须遵守）：

- Pi-compatible model：字段保留 Pi 命名（必要时 Python 端用 snake_case 字段 + `alias=` 输出 Pi-compatible casing），`model_config = ConfigDict(populate_by_name=True, extra="allow")`，确保 `thinkingSignature` / `thoughtSignature` / `textSignature` / `responseId` 等未知或 opaque 字段透传不丢。
- 序列化默认走 `model_dump(by_alias=True, exclude_none=True)`，Round-trip fixture 必须断言往返字节级稳定。
- Discriminated union 优先用协议自带判别字段：`AssistantMessageEvent` 用 `type`，`Message` 用 `role`，`SessionEntry` 用 `type` / `entry_type`。
- 每个跨边界类型（message / event / session entry / JSONL row）都暴露 `TypeAdapter`，给 fixture round-trip、provider adapter、JSONL import/export 共用校验入口。
- Tool argument schema 仍以 JSON Schema 为 contract（extension 提供）；pydantic 只覆盖 NeoMAGI 内建 ToolDefinition 与 boundary payload。
- 仅 NeoMAGI 内部私有模型（无 wire 兼容要求）允许 `extra="forbid"`。
- **Timestamp 双单位强约束**（来自 architecture §`Durable Session Architecture` line 500–505）：
  - `UserMessage.timestamp` / `AssistantMessage.timestamp` / `ToolResultMessage.timestamp` / `BashExecutionMessage.timestamp` / `CustomMessage.timestamp` / `BranchSummaryMessage.timestamp` / `CompactionSummaryMessage.timestamp` 的 Python 类型必须是 `int`（Unix milliseconds）。
  - `SessionHeader.timestamp` 与 `SessionEntryBase.timestamp`（含全部 9 种 entry）的 Python 类型必须是 `str`（ISO8601）。
  - 严禁通过 pydantic validator / `mode="before"` 把 ISO8601 自动转成 `datetime`，也严禁把 Unix ms 自动转成 `datetime`；如需 UI 友好类型，在 model 之外做转换。

**输出文件**：

- `src/ai_provider/types.py`：
  - `TextContent` / `ThinkingContent` / `ImageContent` / `ToolCall`
  - `UserMessage` / `AssistantMessage` / `ToolResultMessage`
  - `Usage`（5 维 + cost 子结构）
  - `Tool` / `Context`
  - `AssistantMessageEvent`（12 帧 discriminated union）
  - `Model`（含 `contextWindow` 必填注释）
  - `StopReason` / `ThinkingLevel` / `CacheRetention` / `Transport` 枚举

- `src/agent_core/types.py`（与 pi-mono `packages/agent/src/types.ts` 边界一致，**只覆盖 core 层**）：
  - `AgentMessage` = `UserMessage | AssistantMessage | ToolResultMessage`，并通过 Python 端的 union 扩展点（如 `Annotated` + `Field(discriminator="role")` + `TypeAdapter` 重新构造）支持 `cli.core` 注入新 role；**4 个 coding 自定义 role（bashExecution / custom / branchSummary / compactionSummary）不在此层声明**。
  - `AgentState`、`AgentEvent`（**10 帧 union**：`agent_start` / `agent_end` / `turn_start` / `turn_end` / `message_start` / `message_update` / `message_end` / `tool_execution_start` / `tool_execution_update` / `tool_execution_end`）。
  - `AgentTool`、`AgentToolResult`、`ToolExecutionMode`
  - `BeforeToolCallContext` / `AfterToolCallContext`、各 `Result` shape

- `src/cli/core/session_types.py`（coding-agent 产品层，扩展 core 类型）：
  - `SessionHeader`、`SessionEntryBase`
  - 9 种 entry：`message` / `thinking_level_change` / `model_change` / `compaction` / `branch_summary` / `custom` / `custom_message` / `label` / `session_info`
  - `SessionContext`、`SessionTreeNode`、`SessionInfo`
  - 4 个自定义 message role：`BashExecutionMessage`（含 `excludeFromContext`）、`CustomMessage`、`BranchSummaryMessage`、`CompactionSummaryMessage`
  - `CodingAgentMessage` = core `AgentMessage` ∪ 上述 4 个 role 的扩展 union（与 Pi `CustomAgentMessages` declaration merging 行为对齐）
  - `AgentSessionEvent` = core `AgentEvent`（10 帧）∪ **5 个 session-level 帧**：`queue_update` / `compaction_start` / `compaction_end` / `auto_retry_start` / `auto_retry_end`，共 15 个 variant
  - `CURRENT_SESSION_VERSION = 3` 常量

- `src/cli/extensions/types.py`：
  - `ExtensionContext` / `ExtensionCommandContext` / `ExtensionUIContext` 协议接口（用 `typing.Protocol`）
  - `ExtensionAPI` 协议接口：覆盖 W1.D 列出的全部 method 与 property，包含 `register_provider` / `unregister_provider` 与 `events: EventBus`
  - `ProviderConfig`（registerProvider 入参 schema：`base_url` / `api_key` / `api` / `models` / `oauth` / `headers` / `auth_header` / `stream_simple`）
  - `EventBus` 协议接口：与 pi-mono `packages/coding-agent/src/core/event-bus.ts` 一致，`emit(channel: str, data: object) -> None` 与 `on(channel: str, handler: Callable[[object], None]) -> Callable[[], None]`（`on` 返回 unsubscribe callback）。Python 命名映射后是 `emit` / `on`；不要发明 subscribe/publish 替代命名，否则 extension parity 会破。
  - `RegisteredCommand`、`RegisteredShortcut`、`RegisteredFlag`、`KeyId`
  - `ToolDefinition`（含 `prepareArguments` 在 schema 校验前的语义注释）
  - 全部 ExtensionEvent payload + 4 个 `SessionBefore*Result`
  - `BeforeAgentStartEventResult`（含 `message` append + `systemPrompt` 链式语义注释）
  - `ToolCallEvent` / `ToolResultEvent` 按 toolName 的 discriminated union

时序：W2 内部按 `ai_provider` → `agent_core` → `cli.core` → `cli.extensions` 顺序，因为后者依赖前者。

### W3. Overflow 与 Usage 归一化

`src/ai_provider/overflow.py`：

- `OVERFLOW_PATTERNS: tuple[re.Pattern, ...]` —— 复刻 pi `packages/ai/src/utils/overflow.ts` 的 19 条正则，逐条标注 provider 来源与 sample error message 出处。
- `NON_OVERFLOW_PATTERNS: tuple[re.Pattern, ...]` —— 3 条排除模式（throttling / rate limit / too-many-requests）。
- `is_context_overflow(message: AssistantMessage, context_window: int | None = None) -> bool`：与 pi 同行为。
- 单测：`tests/test_overflow.py` 用 16 个 provider 的真实 error message sample 跑断言（sample 来自 pi-mono 注释内的 quoted error 文本）。

`src/ai_provider/usage.py`：

- `calculate_cost(model, usage) -> Cost`：按 `cost.input * input/1e6` 等 5 维计算（含 total = sum）。
- `normalize_provider_usage(raw: dict, provider: str) -> Usage`：占位实现 —— W3 只解决"避免 cacheRead 双计"的归一化，对每个 provider 留一个 hook。完整实现在 M2，但本轮要先写 fixture（见 W4）。

### W4. Compatibility Fixtures

目录：`tests/fixtures/pi_compat/<scene>/`，每场景含：

```
README.md            # 来源（pi-mono 哪个测试 / 用例）+ assertion 期望
input.json           # 输入数据，按 Pi mono 协议结构
expected.json        # 期望结果（可选；某些 scene 是 events.jsonl）
events.jsonl         # 时序事件流（适用于 stream/replay 场景）
```

26 条（来自 architecture §`Compatibility Fixtures`）：

| 类别 | Scene | 关键校验点 |
| --- | --- | --- |
| Stream | `assistant_text_delta` / `assistant_thinking_delta` / `assistant_tool_call` | start 必先发；partial 字段累积 |
| Tool | `tool_execution_success` / `tool_execution_error` / `parallel_tools` | tool_execution_end 顺序；error 进入 ToolResultMessage |
| Abort | `abort_during_stream` / `abort_during_tool` | stopReason="aborted"；session 保留 partial |
| Session | `session_tree_branch` / `compaction` / `branch_summary` / `model_change` / `thinking_level_change` | 9 种 entry round-trip + buildSessionContext |
| Extension | `extension_custom_message` / `extension_api_surface` / `extension_tool_event_mutation` / `before_agent_start_chained_systemprompt` / `session_before_compact_extension_replace` | in-place mutation 不二次校验；systemPrompt 链式 |
| RPC | `rpc_prompt_flow` / `rpc_sync_response` | sync vs async 响应二态 |
| Cache | `cache_retention_none` / `session_affinity_headers` / `usage_cache_normalization` | none 模式抹掉所有 cache 字段；header 通道 |
| Overflow | `overflow_error_patterns` / `silent_overflow` | 双路径 + 排除集 |
| Tool args | `prepare_arguments_repair` | 修复后仍走 schema 校验 |

**M0 必须交付完整 input + expected 的 8 条核心 fixture**：

1. `assistant_text_delta`
2. `tool_execution_success`
3. `parallel_tools`
4. `compaction`
5. `cache_retention_none`
6. `overflow_error_patterns`
7. `silent_overflow`
8. `rpc_prompt_flow`

剩余 18 条只交付 `README.md` 占位 + 期望大纲，由 M1–M3 在实现对应能力时补全（fixture 与代码同 PR 提交）。

每条 M0 核心 fixture 必须能被 W2 的 `TypeAdapter` round-trip 还原（pytest 加一个 `test_fixture_round_trip.py`），并满足：

- `model_dump(by_alias=True, exclude_none=True)` 与 fixture 原始 JSON 在去除排序差异后完全一致；
- opaque 字段（`thinkingSignature` / `thoughtSignature` / `textSignature` / `responseId` 等）不被丢弃；
- timestamp 字段**类型保留**：`SessionHeader` / `SessionEntryBase` 中的 `timestamp` 在 dump 后仍是 `str`（ISO8601）；message 类型中的 `timestamp` 在 dump 后仍是 `int`（Unix ms）。绝对禁止 pydantic 把任何 timestamp 字段静默归一化为 `datetime`。

### W5. Pi 基线文件索引（实现 ADR-0011）

ADR-0011 已锁定基线为 `97a38bf6`，并规定升级须经独立 ADR + diff review。W5 不重复政策决策，只把 ADR-0011 落到一份**可被 W1 / W4 / 后续 milestone 反向引用的文件级索引**。

`dev_docs/plans/p1_m0/pi_mono_baseline.md`：

- 引用 ADR-0011 作为权威基线声明。
- pi-mono commit `97a38bf6`、本地 clone 路径、fetch 时间。
- 关键文件 + line range 索引，与 W1 behavior matrix 的每一条 entry 双向引用：
  - `packages/ai/src/types.ts`（types / events / Usage / Model）
  - `packages/ai/src/utils/event-stream.ts`、`utils/overflow.ts`
  - `packages/ai/src/providers/{anthropic,openai-responses,openai-completions,amazon-bedrock,faux}.ts` 的 cache / overflow / usage 实现行段
  - `packages/agent/src/{types,agent,agent-loop}.ts`
  - `packages/coding-agent/src/core/{agent-session,session-manager,messages,slash-commands,settings-manager,auth-storage,model-registry,resource-loader,prompt-templates,skills}.ts`
  - `packages/coding-agent/src/core/compaction/*.ts`
  - `packages/coding-agent/src/core/extensions/types.ts`（ExtensionAPI / UI / Events / Result shapes）
  - `packages/coding-agent/src/core/tools/*.ts`（含 `file-mutation-queue.ts`）
  - `packages/coding-agent/src/modes/rpc/rpc-types.ts`
  - `packages/tui/src/*.ts`（Component / Editor / Overlay / terminal-image）
- 升级流程指针：明确链接 ADR-0011 §影响段，禁止在 implementation PR 中静默 bump baseline。

### W6. TUI Playback 协议

`design_docs/architecture/tui_playback_format.md`：

按 architecture P1 acceptance "TUI mock playback consumes only `AgentSessionEvent` / `AssistantMessageEvent`" 的硬约束，事件流和 harness 控制必须分离：

- **`events.jsonl`**：每行严格是一条 `AgentSessionEvent` 或 `AssistantMessageEvent`，**不含**任何元数据、时序、注入字段或 `_*` 前缀的私有键。这就是 TUI 真正消费的事件流；W4 fixture round-trip 用 `TypeAdapter` 校验时不需要跳过任何"控制行"。
- **`playback.json`**（同目录 sidecar，可选；仅 M1 mock harness 读取，不进入 TUI）：
  - `version: 1`
  - `scene: str`
  - `speed_multiplier: float = 1.0`
  - `delays_ms: list[int]`，与 `events.jsonl` 行号一一对应；缺省全 0。
  - `injects: list[{"after_event_index": int, "action": "abort" | ...}]`，在指定 event 行后触发 harness 副作用，例如 `abort_during_stream` / `abort_during_tool` 通过 inject `Agent.abort()`。
- M1 mock harness 读 sidecar 拿到延时和 inject 时序，按节奏把 `events.jsonl` 一行行喂给 TUI；TUI 看到的永远是纯 events，与 M2/M3 真实 runtime 的事件流字节级一致。
- M2/M3 runtime 测试可以单独用 `events.jsonl` 而不读 sidecar。

### W7. 进度归档

按 `dev_docs/progress/README.md` 的 append-only 单文件政策，M0 收尾不新增 phase 文件，分两件落地：

- **canonical 进度账**：在 `dev_docs/progress/progress.md` 末尾追加一条 P1-M0 closeout 条目，按 README 模板写 `Status / Done / Evidence / Next / Risk`，`Evidence` 至少含 closeout 文档路径与 W0–W6 关键 commit。
- **里程碑 closeout**：在 `dev_docs/plans/p1_m0/closeout.md` 放 M0 内部追踪细节，避免污染全局账：
  - 每条 W0–W6 的状态、commit hash、PR 编号。
  - 已知偏离与原因（例如某个 fixture 推迟到 M2）。
  - "Upstream observed but deferred" 段：M0 期间发现的 pi-mono `97a38bf6` 之后的行为变化，按 ADR-0011 默认 deferred，记录线索供未来 baseline 升级 ADR 引用。
  - 下一里程碑（M1）启动前置条件检查清单。

## 完成标准（Acceptance）

M0 视为完成需同时满足：

1. `just lint` 通过（justfile 已切到 `infra.complexity_guard`），complexity ratchet 无回退。
2. `pyproject.toml` 含显式 `[build-system]` = hatchling、列出 7 个 `src/` 顶级包、把 `pydantic>=2,<3` 列为生产依赖；`uv sync` 成功；`uv run python -c "import ai_provider, agent_core, cli.core, cli.extensions, tui, storage, policy, infra"` 不报错。
3. `pi_behavior_matrix.md` 覆盖 9 大区块（A–I），且每条 entry 都有 pi-mono 文件路径引用；ExtensionAPI 区块逐行枚举所有 method 与 property，包含 `register_provider` / `unregister_provider` / `events: EventBus`。
4. `ai_provider.types`、`agent_core.types`、`cli.core.session_types`、`cli.extensions.types` 全部按 ADR-0010 实施约束暴露 pydantic v2 模型与 `TypeAdapter`；Pi-compatible 模型默认 `extra="allow"`、alias 序列化往返保真；`AgentEvent` 在 core 层 10 帧、`AgentSessionEvent` 在 cli 层 15 帧；4 个 coding 自定义 role 只在 `cli.core` 层声明。
5. `is_context_overflow` 对 16 个 provider 的 sample error message 全部返回正确判定（`pytest tests/test_overflow.py` green）。
6. `tests/fixtures/pi_compat/` 26 个目录全部存在；其中 8 条核心 fixture 含完整 `input` + `expected` 并通过 `test_fixture_round_trip.py`，opaque 字段透传不丢、timestamp 字段类型保持原状（int 仍是 int，ISO8601 仍是 str）。
7. `pi_mono_baseline.md`、`tui_playback_format.md`、`progress/p1_m0.md` 三份文档存在并入库；`pi_mono_baseline.md` 引用 ADR-0011 并与 behavior matrix 的 entry 双向链接；`tui_playback_format.md` 明确 `events.jsonl` 是纯 `AgentSessionEvent` / `AssistantMessageEvent` 流，所有 harness 控制位于 `playback.json` sidecar。

## 顺序与依赖

```
W5 (基线文件索引, 实现 ADR-0011)
  ↓
W0 (包骨架) ──→ W1 (behavior matrix) ┐
                                      ├─→ W4 (fixtures)
W2 (pydantic v2 类型) ─────→ W3 (overflow/usage) ┘
                ↓
              W6 (playback 协议)
                ↓
              W7 (progress 归档)
```

W5、W0、W1 三件事彼此独立，可以并行起手；W2 是 critical path 上最重的一块（类型选型已由 ADR-0010 锁定，无需试样）。

## 风险

- **Fixture 蔓延**：26 条 fixture 全产出工作量大。约束 M0 仅交付 8 条核心 + 18 条占位，剩余跟随 M1–M3 实现。
- **pydantic 默认配置回退**：ADR-0010 规定 Pi-compatible 模型必须 `extra="allow"` + alias 透传，否则 `thinkingSignature` / `thoughtSignature` / `responseId` 等会被悄悄丢弃。W2 模板代码必须把这一 ConfigDict 默认值固化到 mixin 或 base class，避免后续模型作者忘配置。
- **alias 序列化回环**：`model_dump(by_alias=True)` 与 `model_validate` 必须配对正确；fixture round-trip 需要断言完全字节级稳定（去除 key 顺序差异），否则 cross-provider handoff 会失败。
- **复杂度 ratchet**：W2 引入大量类型声明会触发 complexity_guard。建议提前 `just complexity-baseline` 在干净状态下刷一次。
- **基线漂移诱惑**：开发期间发现 pi-mono upstream 修复了缺陷或新增了行为，按 ADR-0011 默认入 backlog，不在 implementation PR 内静默吸收。M0 closeout（`dev_docs/plans/p1_m0/closeout.md`）必须留一段记录"upstream observed but deferred"，作为未来 baseline 升级 ADR 的证据来源。

## 后续移交

M0 完成后立刻把以下 artifact 交给 M1–M3：

- `ai_provider.types.AssistantMessageEvent` → M1 mock playback、M2 真实 provider stream output。
- `agent_core.types.AgentEvent` → M1 TUI 渲染契约、M3 agent loop 输出。
- `cli.core.session_types.SessionEntry` → M6 Postgres schema、M10 export。
- `tests/fixtures/pi_compat/` → 全 milestone 的回归测试基线。
- `tui_playback_format.md` → M1 mock harness 直接实现。
- `pi_behavior_matrix.md` → M5/M8/M9 的产品验收清单。

Architecture 文档在 M0 期间冻结；任何对 contract 的修改必须先回 architecture，再回此 plan。
