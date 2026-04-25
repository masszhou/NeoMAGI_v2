---
doc_id: 019dc674-0d7d-76c0-9737-70dae4090945
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T23:03:01+02:00
---
# P1-M1 Implementation Plan: TUI Skeleton + Mock Playback Harness

- Status: draft
- Date: 2026-04-25
- Roadmap: `design_docs/roadmap/p1_engine_pi.md` (§ P1-M1)
- Architecture: `design_docs/architecture/p1_pi_cli_technical_architecture.md`
  § TUI Contract (line 944–982) · § P1 Implementation Acceptance (line 1159–1170)
- Behavior matrix: `design_docs/architecture/pi_behavior_matrix.md`
  § A (slash command 全表) · § F.1 (TUI / shell / images / skills 默认值)
- Playback contract: `design_docs/architecture/tui_playback_format.md`
- Pi-mono baseline: `97a38bf6` (ADR-0011)；`pi_mono_baseline.md` 已固化关键文件索引
- Governing decisions:
  - ADR-0009 Pi CLI product equivalence contract
  - ADR-0010 Pydantic v2 protocol types
  - ADR-0013 Python async for Pi promise extension methods
  - ADR-0014 Extend async protocol rule to extension UI context
- New decisions to land inside M1:
  - ADR-0015 Native ANSI TUI runtime（W0 起草、accepted 后才能进入 W2/W3）—— 收敛
    architecture § Open Design Questions line 1157 "UI framework choice"

## 目标

落实 P1-M1：交付一个**可启动、可输入、可退出、终端能恢复**的 NeoMAGI CLI TUI
skeleton，以及一个能把 M0 的 `events.jsonl` + `playback.json` sidecar 真实播放
出来的 mock harness。M1 完成后：

- 用户运行单条命令即可进入 TUI，看到接近最终 CLI 的壳；
- TUI 渲染层只消费 `AgentSessionEvent` / `AssistantMessageEvent`（M0 的 15 + 12
  帧），不引入任何 UI-only 协议；
- 通过 `--playback <fixture>` 或内置 `/play` 命令，把 8 条核心 fixture 中的
  `assistant_text_delta` / `tool_execution_success` / `parallel_tools` /
  `compaction` 等场景播放出来；
- 退出（正常 `/quit`、Ctrl+C、Esc-Esc、SIGTERM、异常崩溃）后终端状态、光标、
  bracketed paste、raw mode 全部恢复；
- abort 后 UI 保留 partial/error 状态并恢复输入（roadmap M1 完成标准的 negative
  test）。

M1 **不**做：真实 provider 连接（M2）、真实 agent loop（M3）、真实工具执行
（M5）、Postgres session 持久化（M6）、compaction / branch summary 计算（M7）、
extension / skills / prompt template 加载（M8）、settings / auth / model selector
持久化（M9）、JSONL export 落盘（M10）。这些层在 M1 全部以 stub 或 fixture 替代。

## 范围

### In scope

- ADR-0015 起草并 accepted：TUI runtime stack 选型（native ANSI runtime +
  `wcwidth`；不引入额外 formatter 依赖）。
- CLI 入口：`uv run neomagi`（`pyproject.toml [project.scripts]`）+
  `python -m cli` 双入口；首启动进入 interactive TUI，`--playback <dir>` /
  `--print` / `--help` 三个 P1 必备 flag 占位。
- 终端 lifecycle：raw mode、bracketed paste、SIGWINCH resize、SIGINT/SIGTERM、
  `try/finally` + `atexit` 兜底恢复；崩溃路径 dump 一段错误 trailer 后再恢复
  终端（与 Pi `pi-tui` `Component` lifecycle 对齐，见 `pi_mono_baseline.md`
  `packages/tui/src/*` 引用）。
- Editor：多行 prompt、prompt history、Shift+Enter 换行、bracketed paste、
  常用光标移动、长粘贴、中文 IME 与宽字符基本可用性。
- 提交语义（与 architecture line 974–982 一致）：
  - Enter（idle）→ submit；
  - Enter（streaming）→ queue steering（M1 仅入队，event 上仍是 stub）；
  - Alt+Enter → queue follow-up（同上）；
  - Esc → abort；
  - 双 Esc → 按 settings.doubleEscapeAction（M1 默认 `none`，仅触发占位通知）；
  - `/` → slash autocomplete；
  - `@` + Tab → 文件 fuzzy 搜索；
  - `!cmd` / `!!cmd` → user bash mode 视觉状态（M1 仅渲染 placeholder
    `BashExecutionMessage`，真正执行在 M5）；
  - Ctrl+V → 图片粘贴入口；M1 默认降级为 placeholder `ImageContent` 引用，
    实际终端图片协议留 M2/M5。
- Renderer 套件（架构 line 959–971 表格逐行映射）：
  - `UserMessage` 渲染器；
  - `AssistantMessage` streaming 渲染器（text / thinking / toolCall partial
    更新，按 `text_delta` / `thinking_delta` 累积）；
  - `ToolResultMessage` 渲染器（tool 名 + 参数摘要 + 结果摘要 + truncation/
    is_error 标志）；
  - `BashExecutionMessage` 渲染器（含 `excludeFromContext=true` 视觉差异）；
  - `CompactionSummaryMessage` / `BranchSummaryMessage` 渲染器；
  - `queue_update` / `compaction_start` / `compaction_end` /
    `auto_retry_start` / `auto_retry_end` → status / notification 组件；
  - 极简 markdown + code block 渲染（plain text、行内代码、围栏代码、列表、
    heading；inline image 走 placeholder fallback）。
- Overlay / selector 框架：focus 栈、anchor、hide/show、Esc 关闭；P1 必备
  overlay：`session selector` 占位、`model selector` 占位、`settings list`
  占位、`confirm` 弹窗、`loader` / `cancellable loader`。M1 仅交付框架与 1–2
  个示例 overlay，session/model 实际数据由 M6/M9 接入。
- Slash command 占位（仅 M1 闭环可用的子集，其余在 behavior matrix § A 标注
  `[stub]`）：`/new`、`/quit`、`/hotkeys`、`/play <fixture>`（M1 专属，仅
  `--playback` 模式 / debug 用），其余命令 autocomplete 列表能显示但执行返回
  "not implemented in M1"。
- Mock playback harness（落实 `tui_playback_format.md` §4）：
  - 加载 `events.jsonl`，逐行 `AgentSessionEventAdapter` /
    `AssistantMessageEventAdapter` `validate_python`；
  - 加载同目录 `playback.json` sidecar（缺失则使用全 0 delays + 空 injects）；
  - 按 `delays_ms[i] * speed_multiplier` 节奏投递事件；
  - 处理 `inject.action == "abort"`（M1 必须）/`user_input`（M1 必须）/
    `resize` / `quit`（M1 全部支持，与 sidecar schema 对齐）；
  - 投递完毕后等 TUI idle 再退出。
- Negative test 覆盖：
  - `abort_during_stream` fixture：abort 后 partial 文本仍显示，editor 重新
    可输入；
  - `abort_during_tool` fixture：tool 行渲染为 `aborted`，editor 重新可输入；
  - 终端恢复测试：playback 退出后 `tput cnorm` / bracketed paste 关闭、
    cursor 可见、rawmode 关闭。
- 单元 + 集成测试：
  - `tests/tui/test_lifecycle.py`：lifecycle 启动 / 退出 / 信号 / 异常恢复；
  - `tests/tui/test_editor.py`：editor 提交语义、bracketed paste、宽字符；
  - `tests/tui/test_renderers.py`：每个 renderer 输入 fixture event → 输出
    snapshot；
  - `tests/tui/test_playback_harness.py`：读 8 条核心 fixture 中适用于 M1 的
    子集（assistant_text_delta / tool_execution_success / parallel_tools /
    compaction / abort_during_stream / abort_during_tool）→ 跑通完整播放；
  - `tests/tui/test_event_adapter.py`：`events.jsonl` 任意行经过 TUI 适配后
    路由到正确的 renderer。

### Out of scope（属于 M2+）

- 真实 provider / faux provider 实现（M2）。
- `agent_core.Agent` runtime（M3）。
- TUI ↔ runtime 真实接线（M4）。
- 任何工具的真实执行 / policy（M5）。
- Postgres session manager / resume / fork / tree（M6）。
- compaction / branch summary 计算（M7，M1 仅渲染 fixture 中已有的
  `CompactionSummaryMessage` / `BranchSummaryMessage`）。
- extension loader、skill / prompt template 注入（M8）。
- settings / auth storage / model registry 持久化（M9，M1 用内存默认值）。
- 完整 inline image 终端协议（Kitty / iTerm / sixel）：M1 一律 placeholder。
- `/share`、JSONL export 落盘（M10）。

## 工作分解

| ID | 工作项 | 产出 |
| --- | --- | --- |
| W0 | ADR-0015 TUI runtime stack 选型 | `design_docs/decisions/0015-tui-runtime-stack.md` |
| W1 | CLI 入口 + 终端 lifecycle | `src/cli/__main__.py`、`src/tui/app.py`、`src/tui/lifecycle.py` |
| W2 | Editor + 输入语义 | `src/tui/editor.py`、`src/tui/keymap.py`、`src/tui/autocomplete.py` |
| W3 | Component / 渲染层 + overlay 栈 | `src/tui/components/`、`src/tui/overlay.py`、`src/tui/markdown.py`、`src/tui/image.py` |
| W4 | Event-to-renderer 适配器 | `src/tui/event_router.py` |
| W5 | Mock playback harness | `src/tui/playback.py`、`src/cli/cli_args.py` 中的 `--playback` flag |
| W6 | M1 闭环 slash command 占位 | `src/cli/slash_commands/` skeleton + `/new` / `/quit` / `/hotkeys` / `/play` |
| W7 | 测试套件 + negative test | `tests/tui/test_*.py` |
| W8 | 进度归档 + closeout | `dev_docs/progress/progress.md` 追加 + `dev_docs/logs/p1_m1_closeout.md` |

### W0. ADR-0015 — Native ANSI TUI runtime

收敛 architecture § Open Design Questions line 1157 "UI framework choice"。
默认选择：

- **native ANSI TUI runtime**：自研 `TerminalSession`、`StdinBuffer`、
  key parser、`Component`、focus、overlay、line diff renderer，贴近 Pi
  `packages/tui` 的 runtime 形态。
- **`wcwidth`**：唯一新增生产依赖，用于 CJK / emoji / combining mark /
  ANSI-aware width、slice、truncate、wrap。
- M1 不引入额外 formatter 依赖；markdown / code block 先用极简 formatter 或
  plain text fallback。

替代方案在 ADR 中需要列出并记录拒绝理由：

- `prompt_toolkit`：成熟、async-first、编辑器能力强；但会引入自己的
  `Application` / layout / focus / key binding / buffer 抽象。NeoMAGI 的
  目标是实现并扩展 Pi-style TUI substrate，而不是在第三方 TUI framework 上
  模拟 Pi。
- `textual`：自带 widget 系统会与 Pi `Component` 契约冲突；DOM/CSS 抽象与
  fixture round-trip 难以对齐；定位偏 "桌面 app"。
- `urwid`：维护活跃度低；async 集成要靠适配层。
- `rich` formatter：M1 闭环不需要 formatter 质量；额外依赖增加治理成本与
  snapshot 漂移。未来如 markdown/code 展示成为瓶颈，再单独 ADR 评估。

ADR-0015 必须写明：

1. 不允许 terminal/input/formatter 事件模型穿透 `event_router` 边界；W4 的
   输入仍是 M0 的 pydantic union。
2. 退出路径、信号、SIGWINCH 必须由 NeoMAGI `TerminalSession` 包装；恢复必须
   经过显式 `try/finally`。
3. inline image 协议探测放 W3，先 placeholder，未来不会因为换 TUI 库重写。
4. 与 `complexity_guard` 的 ratchet 兼容：除 `wcwidth` 外不新增 M1 runtime
   依赖。

W0 完成前 W2/W3/W4/W5 不能开工（W1 lifecycle 需要先确定 native runtime
安全封装形态）。

### W1. CLI 入口 + 终端 lifecycle

新增：

- `src/cli/__main__.py`：`python -m cli` 入口；解析 argv → 路由到 interactive /
  playback / print（占位）/ `--help`；不直接启动 TUI，而是把控制权交给
  `tui.app.run()`。
- `pyproject.toml [project.scripts]`：`neomagi = "cli.__main__:main"`。
- `src/tui/app.py`：单例 `TUIApp`，组合 `TerminalSession`、`StdinBuffer`、
  `Renderer`、focus root；公开 `run()` / `dispatch_event()` /
  `inject_user_input()`。
- `src/tui/lifecycle.py`：
  - `enter()`：raw mode、bracketed paste on、隐藏光标（按 settings）、注册
    SIGINT/SIGTERM/SIGWINCH handler；
  - `exit(restore=True)`：bracketed paste off、显示光标、关闭 raw mode、
    drain stdin、刷写 ANSI reset；
  - 异常 trailer：捕获未处理异常 → 写一段简短 traceback 到 stderr → 仍走
    `exit(restore=True)`；
  - `atexit` + `signal` 双兜底，确保 SIGTERM / unhandled exception 也恢复
    终端。

接受标准：在 macOS Terminal / iTerm2、Linux xterm 上启动 `neomagi`，
`Ctrl+C`、`/quit`、`kill <pid>`、`raise SystemExit` 四条退出路径终端均能恢复
正常状态（cursor 可见、bracketed paste 关闭、`stty -a` 显示 cooked mode）。

### W2. Editor + 输入语义

新增：

- `src/tui/editor.py`：native `EditorState`；多行模式；prompt history（M1
  仅内存，M9 接入 settings）；bracketed paste 标记保留；宽字符 caret 用
  `wcwidth`。
- `src/tui/keymap.py`：架构 line 972–982 的输入语义全部入键位表；标注
  "core 不可让渡键位"（Esc / Enter / Alt+Enter / Ctrl+C / Ctrl+L / Ctrl+P
  / Tab / `/` / `@` / `!`），M8 extension keybinding 注册时不能覆盖。
- `src/tui/autocomplete.py`：
  - slash autocomplete：从 W6 注册的 command 列表读取（按 architecture
    line 451–477 提示优先级），M1 实际命令列表只有 `/new` / `/quit` /
    `/hotkeys` / `/play`，但 autocomplete 必须能展示 behavior matrix § A 中
    全部 21 条 Pi 内建命令（标注 `[stub]`）；
  - 文件 fuzzy 搜索：`@` 触发，按 cwd 走广度优先 + 内置轻量 scorer；M1
    限制返回前 50 条，避免大仓库卡顿，不新增依赖。
- 提交流程：状态机 `idle | streaming | aborting`，M1 用 mock playback 状态
  驱动；提交后调用 `playback.submit_user_prompt(text)` 触发对应 fixture 的
  播放（详见 W5）；非 playback 模式下显示 placeholder 通知 "M1 mock — no
  agent runtime; pass --playback or use /play"。

### W3. Component / 渲染层 + overlay 栈

新增：

- `src/tui/components/__init__.py`：`Component` 抽象（`render(width) -> list
  [str]`、可选 `handle_input(data)`、可选 `cursor_marker`），与 architecture
  line 950–957 一致。
- `src/tui/components/{user_message,assistant_message,tool_execution,
  bash_execution,custom_message,compaction_summary,branch_summary,status}
  .py`：架构 line 959–971 表格逐行实现。
- `src/tui/markdown.py`：极简 markdown formatter（plain text、heading、
  list、inline code、fenced code block），不引入 `rich`；行宽按 viewport
  截断。
- `src/tui/image.py`：inline image placeholder（M1 一律渲染为
  `[image: <path-or-id> <w>x<h> (terminal preview unavailable)]`）；终端协议
  探测留接口 `detect_protocol()`（M2/M5 接入 Kitty / iTerm / sixel）。
- `src/tui/overlay.py`：focus 栈、anchor (`top-left` / `bottom-left` /
  `center`)、size、`hide()` / `show(component)`、Esc 关闭顶层；提供 `Loader`
  / `CancellableLoader` / `Selector` / `Confirm` / `SettingsList` 5 个示例
  overlay；M1 只在 `/play` selector、`/quit` 确认、streaming `Loader` 三处
  实际使用。

差分渲染：M1 实现 line diff renderer，并用 synchronized output（`CSI ? 2026
h/l`）包裹批量写入；遇到 resize / width change / shrink clear 走 full redraw。

### W4. Event-to-renderer 适配器

新增 `src/tui/event_router.py`：

- 入口：`route(event)` 接收一个 pydantic `AgentSessionEvent` 或
  `AssistantMessageEvent` 实例；
- 内部 dispatch 表（discriminator 字段 `type`）：
  - `agent_start` / `agent_end` / `turn_start` / `turn_end` → 状态机迁移
    （`idle → streaming → idle`），无可见组件；
  - `message_start` → 在消息列底新建对应 `Component`（按 message role 选
    `UserMessage` / `AssistantMessage` / `ToolResultMessage` /
    `BashExecutionMessage` / `CustomMessage` / `BranchSummary` /
    `CompactionSummary`）；
  - `message_update` → 把 payload `AssistantMessageEvent` 分发给当前
    `AssistantMessage` 组件的 `apply(event)`，由组件累积 partial text /
    thinking / toolCall；
  - `message_end` → 组件标记完成，stopReason / usage / cost 写入 footer 摘要；
  - `tool_execution_start` / `update` / `end` → 在对应 turn 下渲染
    `ToolExecution` 组件；M1 展示 tool 名、参数 JSON 摘要（截断 200 字符）、
    结果摘要、`is_error` / `truncated` / `duration`；
  - `queue_update` / `compaction_start` / `compaction_end` /
    `auto_retry_start` / `auto_retry_end` → 顶部 status notification（限时
    显示 + 写入历史 log）。
- 不允许 router 内识别任何不在两个 union 中的字段；遇到未知 event 抛
  `RuntimeError("contract violation: ...")`，与 architecture acceptance line
  1163 一致。

W4 必须用 M0 已有的 8 条核心 fixture 全部 round-trip 一遍：每条 event 经过
`AgentSessionEventAdapter` / `AssistantMessageEventAdapter` 验证 → `route` →
不抛错。这条作为 `tests/tui/test_event_adapter.py` 的 acceptance。

### W5. Mock playback harness

新增 `src/tui/playback.py`：

- `PlaybackHarness(events_path: Path, sidecar_path: Path | None)`：
  - 启动时一次性 `validate_python` 全部 events，任何行失败立即抛错；
  - 校验 sidecar `version == 1`；
  - `delays_ms` 长度必须等于 events 行数，否则拒绝运行；
  - 异步任务循环：
    1. `await asyncio.sleep(delays_ms[i] * speed_multiplier / 1000)`；
    2. `app.dispatch_event(events[i])`；
    3. 处理所有 `inject.after_event_index == i`：
       - `abort` → 调用 `app.handle_abort()`（M1 stub：把当前 streaming
         组件标记 `aborted` + 状态机切回 `idle`）；
       - `user_input` → `app.inject_user_input(inject.text)`；
       - `resize` → 触发 SIGWINCH 模拟 + 重排版；
       - `quit` → `app.exit()`。
- 不向 fixture 目录写任何文件；播放过程 dump 走 `tmp/m1_playback_<ts>.log`。
- 入口：
  - `neomagi --playback tests/fixtures/pi_compat/assistant_text_delta`
    → 进入 TUI、playback 立即开始；
  - 交互内 `/play <fixture-name>` → selector overlay 列出
    `tests/fixtures/pi_compat/` 子目录、确认后启动 harness。
- 与 W2 editor 的关系：`--playback` 模式下提交 prompt 等于"播放下一段"或
  忽略（按 sidecar `injects` 中的 `user_input` 决定）；不会有真实模型回复。

### W6. M1 闭环 slash command 占位

新增 `src/cli/slash_commands/`：

- `__init__.py` 暴露 `register_builtin_commands(registry)`；
- 每条命令一个文件（M1 实际实现 4 条：`new.py` / `quit.py` / `hotkeys.py`
  / `play.py`），其余按 behavior matrix § A 注册一个返回 "not implemented
  in M1; tracked in M{X}" 的 stub，确保 autocomplete 完整且占位指向真正的
  里程碑（如 `/compact` → M7、`/resume` → M6、`/login` → M9）。
- `/new`：清空当前 message 列、状态机回到 idle；M1 不调用 session
  manager（M6）。
- `/quit`：触发 W1 lifecycle exit，先弹 `Confirm` overlay。
- `/hotkeys`：用 `SettingsList` overlay 展示架构 line 972–982 的全部按键。
- `/play`：W5 入口（仅在 fixture 目录存在时显示）。

slash command 注册入口必须**先于** W8 extension API 的 registry，避免后续
M8 引入冲突（M1 不能为 extension 兜底，否则 M8 重写成本变大）。

### W7. 测试套件 + negative test

最低 acceptance 测试：

- `tests/tui/test_lifecycle.py`：
  - 进入 / 退出回合可重入；
  - SIGINT / SIGTERM / 异常崩溃 → `stty -a` 检查 cooked mode 恢复；
  - bracketed paste off 验证（写入 `^[[200~test^[[201~` 后下一次写入仍是
    cooked）。
- `tests/tui/test_editor.py`：
  - Enter / Shift+Enter / Alt+Enter / Esc / 双 Esc 分别触发预期 action；
  - 中文输入（"你好"）caret 列号 = 4；
  - bracketed paste 多行 → 整段进入 buffer 不被逐字解释。
- `tests/tui/test_renderers.py`：
  - 7 类组件分别用 fixture 数据 → render 结果 snapshot；
  - markdown 围栏代码块换行不超 viewport width。
- `tests/tui/test_event_adapter.py`：M0 8 条核心 fixture 的每行 event →
  `route()` 不抛错；遇到伪造的未知 `type` → 抛 `RuntimeError`。
- `tests/tui/test_playback_harness.py`：
  - `assistant_text_delta` 完整 playback → 最终 message 内容 = `Hello, world.`；
  - `tool_execution_success` → tool 行渲染包含 tool name + 结果摘要；
  - `parallel_tools` → 至少两个 tool 行同时出现在 streaming 状态；
  - `compaction` → `CompactionSummaryMessage` 组件渲染并保留摘要文本；
  - `abort_during_stream`（fixture 由 M0 占位 → M1 与 PR 同 commit 补完
    `events.jsonl` + sidecar `inject: abort`）→ partial 文本仍可见、editor
    重新 idle；
  - `abort_during_tool`（同上）→ tool 行标记 `aborted`、editor 重新 idle。

`tests/tui/conftest.py`：用 pipe / pseudo-tty friendly 的 fake terminal 跑无头
TUI；snapshot 使用仓库内自写 stable diff，避免为 M1 引入 snapshot 依赖。

### W8. 进度归档 + closeout

按 `dev_docs/progress/README.md` + `dev_docs/logs/README.md` 约定：

- `dev_docs/progress/progress.md` 末尾追加一条 P1-M1 closeout 摘要条目，
  `Evidence` 至少含 `dev_docs/logs/p1_m1_closeout.md` + W0–W7 关键 commit；
- `dev_docs/logs/p1_m1_closeout.md` 写：
  - 每条 W0–W7 状态、commit、PR；
  - 偏离与原因（典型：某 overlay / 某 inline image 协议推迟到 M2/M5）；
  - "Upstream observed but deferred"：M1 期间发现的 pi-mono `97a38bf6` 之后
    `packages/tui/` 任何变化（默认 deferred，按 ADR-0011 入 backlog）；
  - M2 / M3 / M4 启动前置条件检查：`AssistantMessageEventAdapter` 已被 TUI
    consume、`AgentEventAdapter` 已被 router 接受、playback harness 路径
    可被 runtime 替换。

`just md-doc-header` 必须对所有新增 markdown（ADR-0015、closeout、本 plan）
应用。

## 完成标准（Acceptance）

M1 视为完成需同时满足：

1. **ADR-0015 accepted**：TUI runtime stack 锁定，`wcwidth` 列入
   `pyproject.toml`，`uv sync` 成功，`just lint` green、`complexity_guard`
   0 regression（如必要先 `just complexity-baseline`）。
2. **CLI 可启动**：`uv run neomagi`、`python -m cli` 两个入口都能进入 TUI；
   `uv run neomagi --help` 列出 `--playback` / `--print` / `--help`；
   `--print` 在 M1 仅返回 "not implemented in M1"（占位由 M9/M10 接入）。
3. **终端可恢复**：`Ctrl+C` / `/quit` / `kill <pid>` / 抛出 `SystemExit` /
   `raise RuntimeError` 五种退出路径全部恢复 cooked mode、显示光标、关闭
   bracketed paste（`tests/tui/test_lifecycle.py` green）。
4. **输入语义齐全**：editor 正确响应 Enter / Shift+Enter / Alt+Enter / Esc /
   双 Esc / `/` / `@` / `!` / `!!` / Ctrl+V / Tab；中文输入与 caret 列号
   匹配（`tests/tui/test_editor.py` green）。
5. **Renderer 套件全 green**：架构 line 959–971 表格的 8 行 renderer 全部
   实现并通过 snapshot 测试（`tests/tui/test_renderers.py` green）。
6. **Event router 契约**：`tests/tui/test_event_adapter.py` 在 M0 8 条核心
   fixture 上 100% green；伪造的未知 `type` 触发 `RuntimeError`，确保
   architecture acceptance line 1163 不会回退。
7. **Playback harness 可运行**：6 条 fixture（`assistant_text_delta` /
   `tool_execution_success` / `parallel_tools` / `compaction` /
   `abort_during_stream` / `abort_during_tool`）全部播放成功；
   `abort_during_stream` 与 `abort_during_tool` 满足 negative test 要求
   （partial / error 状态保留 + editor 恢复 idle）。
8. **Slash command 占位完整**：autocomplete 列表覆盖 behavior matrix § A
   全部 21 条 Pi 内建命令（含 `[stub]` 标注）；`/new` / `/quit` /
   `/hotkeys` / `/play` 真正可执行。
9. **不引入 UI-only 协议**：`tui` 包内**禁止**定义自己的 event /message
   类型；所有事件类型只能从 `agent_core.types` / `cli.core.session_types` /
   `ai_provider.types` import。`tests/tui/test_event_adapter.py` 包含一条
   静态扫描断言：`tui` 包不导入 `pydantic` 来定义新模型。
10. **进度归档落地**：`dev_docs/progress/progress.md` 追加 closeout 条目；
    `dev_docs/logs/p1_m1_closeout.md` 与本 plan 一对一；ADR-0015 入库。

## 顺序与依赖

```
W0 (ADR-0015 TUI runtime stack)
  ↓
W1 (CLI 入口 + lifecycle) ──┬─→ W2 (editor + 键位)
                            ├─→ W3 (component / overlay)  ──┐
                            └─→ W4 (event router) ──────────┤
                                                            ├─→ W5 (playback harness)
                                                            │
                                W6 (slash command 占位) ────┘
                                                            ↓
                                                          W7 (测试)
                                                            ↓
                                                          W8 (closeout)
```

W0 是关键路径瓶颈，accepted 之前 W2–W5 不能开工。W2/W3 可并行；W4 依赖
W3（renderer 必须先存在）；W5 依赖 W4；W6 在 W2 之后即可启动；W7 收口；
W8 最后。

## 风险

- **Native ANSI runtime 成本**：会自写 terminal lifecycle、stdin buffer、
  key parser 和 diff renderer。M1 只支持 macOS Terminal / iTerm2 与 Ubuntu
  常见终端，Windows 明确 deferred；W1 必须先做安全封装再扩 UI。
- **中文 IME / 宽字符回退**：`wcwidth` 提供基础显示宽度；部分终端 combining
  mark / emoji 行为仍可能不同。M1 接受 "基本可用"，corner case 列入
  closeout deferred；M5/M9 视用户反馈再升级。
- **Bracketed paste 离场恢复**：异常退出时如果 `lifecycle.exit()` 没被调用，
  终端会卡在 paste 模式。`atexit` + 信号 + `try/finally` 三重兜底，并由
  `tests/tui/test_lifecycle.py` 强制覆盖五条退出路径。
- **Inline image 协议**：M1 一律 placeholder。任何"先做 Kitty / iTerm 简化
  实现"的诱惑都拒绝，避免 M2 引入真实图片时与 M1 实现冲突。
- **差分渲染闪烁**：M1 line diff 必须用 synchronized output 包裹；如仍出现
  明显闪烁，closeout 中记录终端和 fixture，避免在 renderer 外层临时补丁。
- **Slash command 与 extension 冲突**：M1 必须把 builtin registry 设计成
  M8 extension layer 的子集，否则 M8 重写工作量陡增；W6 实现时优先确认
  `RegisteredCommand` 字段对齐 `cli.extensions.types`。
- **Playback harness 与真实 runtime 的 path drift**：M4 真实接线时，路径
  不能因为 M1 的 mock 设计被锁死。`PlaybackHarness` 必须只用 `app.
  dispatch_event(event)` 这一公开接口，不直接操纵 `tui` 内部状态；
  这条作为 W5 review checklist 的硬约束。
- **`complexity_guard` 抖动**：W3/W4 会引入大量新代码。建议 W3 启动前
  `just complexity-baseline` 在干净状态下刷一次；之后每条 PR 自查 ratchet。
- **依赖膨胀**：M1 只新增 `wcwidth`。`rich`、`rapidfuzz`、`syrupy` 等体验或
  测试便利依赖全部 deferred；若 W2/W3 中途想追加依赖，必须先回 ADR。

## 后续移交

M1 完成后立刻把以下 artifact 交给 M2–M4：

- `tui.event_router.route` → M3 真实 `AgentEvent` 接入点；M4 把 router
  从 `PlaybackHarness` 切换到真实 `Agent.events.subscribe()`。
- `tui.app.TUIApp.dispatch_event` → M4 唯一入口，禁止旁路。
- `tui.playback.PlaybackHarness` → M2 faux provider / M3 agent loop
  回归测试可继续复用，作为 cross-layer 字节级 contract 的根证据
  （architecture acceptance line 1163）。
- `cli.slash_commands.registry` → M8 extension API `registerCommand` 直接
  注册到同一 registry；M1 已保证 builtin / extension 命名空间隔离。
- ADR-0015 → 后续若有 inline image 协议或差分渲染升级，必须先回 ADR
  补充章节，再回本 plan / 后继 milestone plan。

Architecture / behavior matrix / playback format 文档在 M1 期间冻结；任何
对契约的修改必须先回 architecture，再回此 plan。
