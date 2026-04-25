---
doc_id: 019dc674-0d7d-76c0-9737-70dae4090945
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T23:03:01+02:00
---
# P1-M1 Implementation Plan: TUI Skeleton + Mock Playback Harness

- Status: accepted
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
  - ADR-0015 Native ANSI TUI runtime —— 已 accepted；收敛 architecture
    § Open Design Questions line 1157 "UI framework choice"。M1 直接落实
    其影响段，不再做选型评估。

## 目标

落实 P1-M1：交付一个**可启动、可输入、可退出、终端能恢复**的 NeoMAGI CLI TUI
skeleton，以及一个能把 M0 的 `events.jsonl` + `playback.json` sidecar 真实播放
出来的 mock harness。M1 完成后：

- 用户运行单条命令即可进入 TUI，看到接近最终 CLI 的壳；
- TUI 渲染层只消费 `AgentSessionEvent` / `AssistantMessageEvent`（M0 的 15 + 12
  帧），不引入任何 UI-only 协议；
- 通过 `--playback <fixture>` 或内置 `/play` 命令，把 W5 deliverable 表
  中的 7 条 M1 可 playback fixture（`assistant_text_delta` /
  `assistant_thinking_delta` / `tool_execution_success` / `parallel_tools`
  / `compaction` / `abort_during_stream` / `abort_during_tool`）播放
  出来；
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

- 落实 ADR-0015 native ANSI substrate：`TerminalSession`（`terminal.py`）、
  `StdinBuffer`（`stdin_buffer.py`）、`Renderer`（`renderer.py`）、
  width guard（`width.py`）与 `Component` 抽象。`wcwidth` 是唯一新增生产
  依赖；M1 不引入 markdown / code formatter 依赖。
- **包边界**：`src/tui/` 只承载 substrate + generic UI primitives（与
  pi-mono `packages/tui` 一致，做终端 substrate / 组件抽象 / 输入 / overlay
  / diff render）；agent event → message/tool/bash 组件的产品语义映射归
  新建的 `src/cli/interactive/`（M1 暂用名，对应 pi-mono `packages/coding-
  agent/src/modes/interactive`）。M1 内 `src/tui` 禁止 import 任何 message
  role / tool 定义；`src/cli/interactive` 可以 import substrate 与协议
  类型，但不能定义 pydantic agent/session/message 模型。
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
  - 双 Esc → 按 settings.doubleEscapeAction（**默认 `tree`，与 behavior
    matrix § F.1 对齐**；M1 不实现 tree navigation，触发时显示 stub 通知
    "tree navigation not implemented in M1; tracked in M6"。默认值契约
    不能漂移）；
  - `/` → slash autocomplete；
  - `@` + Tab → 文件 fuzzy 搜索；
  - `!cmd` / `!!cmd` → user bash mode 视觉状态（M1 仅渲染 placeholder
    `BashExecutionMessage`，真正执行在 M5）；
  - Ctrl+V → 图片粘贴入口；M1 默认降级为 placeholder `ImageContent` 引用，
    实际终端图片协议留 M2/M5。
- 业务 renderer 套件（架构 line 959–971 表格逐行映射，全部落
  `src/cli/interactive/components/`）：
  - `UserMessage` 渲染器；
  - `AssistantMessage` streaming 渲染器（text / thinking / toolCall partial
    更新，按 `text_delta` / `thinking_delta` 累积）；
  - `ToolResultMessage` 渲染器（调用 `ToolRendererRegistry`：M1 仅 generic
    renderer，输出 tool 名 + 参数摘要 + partial / final result 摘要 +
    `is_error` + 结束后的 `duration_ms`；**不**承诺 truncation 标志 ——
    `truncated` 不是 `ToolExecutionEndEvent` 字段，由 M5 policy 在
    `result.metadata` / `result.details.truncation` 暴露后再加，避免与
    W4 `ToolRenderContext` API 不一致）；
  - `BashExecutionMessage` 渲染器（含 `excludeFromContext=true` 视觉差异）；
  - `CompactionSummaryMessage` / `BranchSummaryMessage` 渲染器；
  - `queue_update` / `compaction_start` / `compaction_end` /
    `auto_retry_start` / `auto_retry_end` → status / notification 组件。
- TUI generic primitives（`src/tui/`）：
  - 极简 markdown + code block formatter（plain text、行内代码、围栏代码、
    列表、heading；inline image 走 placeholder fallback）；
  - inline image placeholder primitive；
  - overlay 框架：focus 栈、anchor、hide/show、Esc 关闭；提供 `Loader` /
    `CancellableLoader` / `Selector` / `Confirm` / `SettingsList` generic
    widget。具体业务实例化（`session selector` 占位、`model selector` 占位、
    `settings list` 占位、`/quit` confirm、streaming loader）由
    `src/cli/interactive` 装配；M1 仅交付框架 + 1–2 个示例装配，session /
    model 实际数据由 M6/M9 接入。
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
- 单元 + 集成测试（按包边界拆 `tests/tui/` substrate vs
  `tests/cli/interactive/` 业务）：
  - `tests/tui/test_lifecycle.py`：lifecycle 启动 / 退出 / 信号 / 异常恢复；
  - `tests/tui/test_editor.py`：editor 提交语义、bracketed paste、宽字符；
  - `tests/cli/interactive/test_renderers.py`：每个业务 renderer 输入
    fixture event → 输出 snapshot；
  - `tests/cli/interactive/test_playback_harness.py`：读 W5 deliverable
    表列出的 7 条 M1 fixture（`assistant_text_delta` /
    `assistant_thinking_delta` / `tool_execution_success` /
    `parallel_tools` / `compaction` / `abort_during_stream` /
    `abort_during_tool`）→ 全部跑通完整播放；
  - `tests/cli/interactive/test_event_router.py`：`events.jsonl` 任意行
    经过 router 后路由到正确的 renderer，含裸 `AssistantMessageEvent`
    top-level path 与 contract violation 抛错。

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
| W0 | Native ANSI substrate（落实 ADR-0015） | `src/tui/terminal.py`、`src/tui/stdin_buffer.py`、`src/tui/renderer.py`、`src/tui/width.py`、`src/tui/component.py`（`Component` 抽象 + `request_render` 回调） |
| W1 | CLI 入口 + 终端 lifecycle | `src/cli/__main__.py`、`src/tui/app.py`、`src/tui/lifecycle.py` |
| W2 | Editor + 输入语义 | `src/tui/editor.py`、`src/tui/keymap.py`、`src/tui/autocomplete.py` |
| W3 | TUI generic primitives | `src/tui/overlay.py`（含 `Loader` / `CancellableLoader` / `Selector` / `Confirm` / `SettingsList` 通用 widget）、`src/tui/markdown.py`、`src/tui/image.py` |
| W4 | Interactive layer：InteractiveController + 业务 components + event_router + ToolRendererRegistry | `src/cli/interactive/__init__.py`、`src/cli/interactive/app.py`（`InteractiveController`，持有 `tui.app.TUIApp` + `EventRouter`）、`src/cli/interactive/components/*.py`、`src/cli/interactive/event_router.py`、`src/cli/interactive/tool_renderer_registry.py` |
| W5 | Mock playback harness | `src/cli/interactive/playback.py`、`src/cli/cli_args.py` 中的 `--playback` flag |
| W6 | M1 闭环 slash command 占位 | `src/cli/slash_commands/` skeleton + `/new` / `/quit` / `/hotkeys` / `/play` |
| W7 | 测试套件 + negative test | `tests/tui/test_*.py`（substrate）+ `tests/cli/interactive/test_*.py`（业务） |
| W8 | 进度归档 + closeout | `dev_docs/progress/progress.md` 追加 + `dev_docs/logs/p1_m1_closeout.md` |

### W0. Native ANSI substrate（落实 ADR-0015）

ADR-0015 已 accepted；W0 直接实现 ADR §影响段列出的四个文件 + `Component`
抽象，作为后续所有 W 的底座。

**`src/tui/terminal.py`** —— `TerminalSession` 集中管理（含 SIGWINCH 单
owner，process-level signal 与 lifecycle 的关系见 W1）：

- raw mode（`tty.setraw` / `termios`）；
- bracketed paste on/off；
- alt screen（按 settings 决定是否启用，M1 默认不进入 alt screen）；
- cursor hide/show；
- **`SIGWINCH` 唯一 owner**：`TerminalSession.install_resize_handler(cb)`
  注册 callback；恢复时还原原 handler。`lifecycle.py` 不重复注册
  SIGWINCH，只通过 `TerminalSession` 暴露的 callback 接收 resize；
- stdin drain on exit；
- 退出恢复必须经过显式 `__exit__` / `try/finally`；
- 业务代码不得直接调用 `termios` / 写 `\x1b[?2004h` 等 escape；任何对
  terminal lifecycle 状态的修改必须经 `TerminalSession`。
- **Keyboard protocol 探测（best-effort）**：进入 raw mode 时尝试启用
  xterm `modifyOtherKeys=2`（`CSI > 4 ; 2 m`）与 Kitty keyboard protocol
  level 1（`CSI > 1 u`）；通过 DA / response 探测能力，能用就用，不能用
  fall back 到普通 ESC 序列。退出时 `CSI < u` / `CSI > 4 ; 0 m` 还原。
  M1 验收为 "common terminals best-effort"：在不支持的终端，
  Shift+Enter / Alt+Enter 可能与 Enter / Esc+Enter 不可区分；closeout 中
  按终端记录实际表现，不阻塞 acceptance。

**`src/tui/stdin_buffer.py`** —— `StdinBuffer` 处理：

- partial ESC、CSI、OSC、APC sequence（不能把半个 escape 当普通输入吐给
  上层）；
- bracketed paste 包络识别（`ESC[200~ ... ESC[201~`），整段作为粘贴事件而
  不是逐字 keystroke；
- xterm `modifyOtherKeys` 与 Kitty keyboard protocol 解析（与 W0
  `terminal.py` 的探测协同），能识别就给上层 `Shift+Enter` /
  `Alt+Enter` 等高级事件名；
- 跨 read 切片续读；
- 解析输出统一类型：本地 dataclass `KeyEvent` / `PasteEvent` /
  `ResizeEvent` / `MouseEvent`（M1 仅前三种）；mouse 留接口，M1 不消费。
  这些 dataclass 是 substrate 内部 IPC，不是 playback 协议、不持久化、
  不进入 `events.jsonl`，因此**不**违反 acceptance 第 9 条对 pydantic
  agent/session/message 模型的禁令。
- 上层 `keymap`（W2）只看高级事件名，不接触 escape sequence。

**`src/tui/renderer.py`** —— `Renderer` 以 ANSI line model 为权威：

- first render（空屏 → 全量写）；
- line diff（按行比对前后帧，仅写差异行）；
- synchronized output 包裹批量写入（`CSI ? 2026 h` / `l`）；
- resize / width change → full redraw；
- content shrink → 主动 clear 旧尾行，避免残影。
- 对外只暴露**单一签名**：`present(frame: list[str], cursor:
  CursorPosition | None = None) -> None`（按 review P2 ④；`CursorPosition`
  是本地 dataclass `(row: int, col: int, visible: bool)`）。frame 由
  `TUIApp` 在 W1 组装；cursor 同步在 frame 写完后通过 `CSI <row>;<col>H`
  移动硬件光标，`visible=False` 或 `cursor is None` 则隐藏光标。所有
  cursor 操作只走这一个入口，业务层禁止直写 cursor escape。

**`src/tui/width.py`** —— width guard：

- `visible_width(s)`：基于 `wcwidth` 计算可视列宽，跳过 ANSI SGR / 控制
  序列；
- `slice_by_columns(s, start, end)`：按列宽切片；
- `truncate_to_width(s, width, ellipsis="…")`；
- `wrap_to_width(s, width)`：ANSI-aware wrapping，不切碎 escape；
- 所有 `Component.render(width)` 输出必须经此模块校验；不允许业务代码用
  `len()` / `textwrap` 当列宽真相。

**`src/tui/component.py`** —— `Component` 抽象（架构 line 950–957 + ADR
§影响 § `Component.render(width)` 输出超宽必须截断或 fail-fast）：

- `render(width: int) -> list[str]`，每行宽度 ≤ `width`，超宽必须截断或
  抛 `ComponentOverflowError`；
- 可选 `handle_input(event)`，输入是 `StdinBuffer` 解析后的 dataclass；
- 可选 `cursor_marker`：返回 `(row, col)` 在自己 `render` 输出中的位置，
  用于 IME / 编辑光标定位；
- `Focusable` 标记接口（focus 栈管理由 W3 `overlay.py` + W1 `app.py` 的
  focus root 协同）；
- **`request_render() -> None`**：组件主动 schedule 一次重绘
  （loader、elapsed timer、overlay animation、IME cursor placement 都依赖
  这条，否则会被迫反向依赖 app 内部）。`TUIApp` 在 `attach(component)`
  时注入 callback；组件不能直接持有 `TUIApp` 引用。

**`Renderer` 与 `Component` 的 cursor / 重绘契约**（W0 substrate 必须
落地，避免业务层反向依赖）：

- 每帧渲染前，`TUIApp` 收集 focused component 的 `cursor_marker`，把
  marker 翻译成绝对终端坐标，构造 `CursorPosition` 实例，调
  `Renderer.present(frame, cursor=cursor_pos)`（无 focused / 无 marker
  时传 `cursor=None`）。Cursor 处理与 frame 写入共享同一次
  synchronized output 包裹，避免出现"光标抖到上一帧位置"；
- `request_render` 入队；下一次 event-loop tick 内合并多次请求为一帧，
  避免 loader / animation 风暴写盘；
- 任何组件不允许直接调用 `Renderer.present`，必须通过 `request_render` +
  `TUIApp` 主循环。

**M1 终端兼容范围**（与 ADR 一致）：macOS Terminal / iTerm2 与 Ubuntu
常见终端（xterm / gnome-terminal / Alacritty / kitty）。Windows 不在 M1
支持范围；后续若要支持需另起 ADR。

W0 完成是 W1–W6 的硬前置；W1 的 lifecycle 直接组合 substrate，不绕过。

### W1. CLI 入口 + 终端 lifecycle

新增：

- `src/cli/__main__.py`：`python -m cli` 入口；解析 argv → 路由到 interactive
  / playback / print（占位）/ `--help`；不直接启动 TUI，而是把控制权交给
  W4 `cli.interactive.app.InteractiveController.run()`。
- `pyproject.toml [project.scripts]`：`neomagi = "cli.__main__:main"`。
- `src/tui/app.py`：generic `TUIApp`，组合 `TerminalSession`、`StdinBuffer`、
  `Renderer`、`overlay` focus 栈。**只**暴露 generic 能力 ——
  `run()` / `exit()` / `attach_root(component)` / `attach_overlay(component)`
  / `set_focus(component)` / `request_render()` / `inject_input(KeyEvent |
  PasteEvent | ResizeEvent)` / `simulate_resize(cols, rows)`。**严禁**
  import `agent_core.types` / `cli.core.session_types` / `ai_provider.types`，
  也**不**承载 `dispatch_event` / `handle_abort` / `inject_user_input`
  这类 agent-aware 接口（按 review P1 ① 拆到 `cli.interactive.app`）。
- `src/tui/lifecycle.py`（在 W0 `TerminalSession` 之上加上 process-level
  兜底，不直接操作 termios，也**不**注册 SIGWINCH —— SIGWINCH 单 owner
  在 `TerminalSession`，lifecycle 通过 `install_resize_handler(cb)` 把
  resize callback 串接到 `TUIApp`）：
  - `enter()`：调 `TerminalSession.__enter__`（raw mode、bracketed paste
    on、隐藏光标按 settings）；注册 SIGINT / SIGTERM handler；通过
    `TerminalSession.install_resize_handler(lambda cols, rows:
    tui_app.simulate_resize(cols, rows))` 把 resize 路由到 TUIApp 唯一
    公开入口 `simulate_resize`（按 review P2 ②；不引入 `on_resize` 第二
    入口）；
  - `exit(restore=True)`：调 `TerminalSession.__exit__`（bracketed paste
    off、显示光标、关闭 raw mode、drain stdin、刷写 ANSI reset、还原
    keyboard protocol、还原原 SIGWINCH handler）；
  - 异常 trailer：捕获未处理异常 → 写一段简短 traceback 到 stderr → 仍走
    `exit(restore=True)`；
  - `atexit` + `signal`（仅 SIGINT / SIGTERM）双兜底，确保 SIGTERM /
    unhandled exception 也恢复终端。

接受标准：在 macOS Terminal / iTerm2 与 Ubuntu 常见终端（xterm /
gnome-terminal / Alacritty / kitty）上启动 `neomagi`，`Ctrl+C`、`/quit`、
`kill <pid>`、`raise SystemExit` 四条退出路径终端均能恢复正常状态
（cursor 可见、bracketed paste 关闭、`stty -a` 显示 cooked mode）。Windows
不在 M1 支持范围。

### W2. Editor + 输入语义

新增：

- `src/tui/editor.py`：native `EditorState`；多行模式；prompt history（M1
  仅内存，M9 接入 settings）；消费 W0 `StdinBuffer` 输出的 `KeyEvent` /
  `PasteEvent`，bracketed paste 标记保留；caret 列号走 W0 `width.
  visible_width`。
- `src/tui/keymap.py`：架构 line 972–982 的输入语义全部入键位表；输入是
  W0 `StdinBuffer` 解析后的高级事件名，**不能**自己处理 escape sequence
  或 paste 包络。标注 "core 不可让渡键位"（Esc / Enter / Alt+Enter /
  Ctrl+C / Ctrl+L / Ctrl+P / Tab / `/` / `@` / `!`），M8 extension
  keybinding 注册时不能覆盖。
  - **键位降级注脚**：Shift+Enter / Alt+Enter 依赖 W0 `terminal.py` 的
    keyboard protocol 探测；不支持的终端会与 Enter / Esc+Enter 不可区分。
    keymap 在初始化时读 `TerminalSession.keyboard_protocol_level`，对
    "无法区分"的键位给出明确降级行为（例：Shift+Enter 不可区分时退化为
    单 Enter 提交，并在 footer 标记 "modifyOtherKeys not supported"）。
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

### W3. TUI generic primitives

substrate（`Component` 抽象、`Renderer`、`width.py`）已在 W0 落地。W3 只
扩 `src/tui/` 的 generic primitives（与 pi-mono `packages/tui` 边界一致），
不放任何 message role / tool 业务语义。

新增：

- `src/tui/markdown.py`：极简 markdown formatter（plain text、heading、
  list、inline code、fenced code block），不引入 `rich`；行宽走 W0
  `width.wrap_to_width`。纯字符串 in / 多行 ANSI out，不知道任何
  message 类型。
- `src/tui/image.py`：inline image placeholder primitive（M1 一律渲染为
  `[image: <path-or-id> <w>x<h> (terminal preview unavailable)]`）；终端
  协议探测接口 `detect_protocol()`（M2/M5 接入 Kitty / iTerm / sixel）。
  接受图片元数据 in / 单个 placeholder block out，不知道 `ImageContent`
  的 pydantic 类型。
- `src/tui/overlay.py`：focus 栈、anchor (`top-left` / `bottom-left` /
  `center`)、size、`hide()` / `show(component)`、Esc 关闭顶层；提供
  generic widget `Loader` / `CancellableLoader` / `Selector` / `Confirm` /
  `SettingsList`，参数化任意 `Component`，本身不携带 fixture / message
  语义。

差分渲染、synchronized output、resize full redraw、shrink clear 全部由
W0 `Renderer` 负责；W3 不重复实现，也不能旁路 `Renderer.present` 直写
终端。

### W4. Interactive layer：业务 components + event_router + ToolRendererRegistry

新建 `src/cli/interactive/` 子包（与 pi-mono `packages/coding-agent/src/
modes/interactive` 边界对应），承载所有"agent event → 业务组件"的产品语义
映射，以及把 generic `tui.TUIApp` 包装成 agent-aware controller。M1 该包
**禁止**定义 pydantic agent/session/message 模型，但可以 import M0 的
`ai_provider.types` / `agent_core.types` / `cli.core.session_types`。

新增：

- `src/cli/interactive/__init__.py`：暴露 `InteractiveController` /
  `EventRouter` / `ToolRendererRegistry`、业务 component 入口。
- `src/cli/interactive/app.py`：`InteractiveController` 持有
  `tui.app.TUIApp` + `EventRouter` + 业务 component 装配。它是 event /
  control plane 的唯一公开 owner（按 review P1 ①，generic `TUIApp` 不再
  承担这两个面）：
  - **构造**：`InteractiveController(tui_app: TUIApp,
    router: EventRouter, ...)`；启动时把消息列 root component、status
    overlay、editor focus 通过 `tui_app.attach_*` 接入。
  - **Event plane**：`dispatch_event(event: AgentSessionEvent |
    AssistantMessageEvent) -> None`，转发给 `router.route(event)`，再由
    router 在 active component 上累积 + `request_render`。
  - **Control plane**：`handle_abort()` / `inject_user_input(text: str)` /
    `simulate_resize(cols: int, rows: int)` / `exit()`。`handle_abort`
    把 active assistant / tool execution 标记 `aborted` + editor 复位
    `idle`；`simulate_resize` 直接调 `tui_app.simulate_resize`，**不**
    通过 SIGWINCH，避免污染 process-level signal owner。
  - **`run()`**：driver 入口；`cli.__main__` 调它而不是 `tui_app.run()`
    直连。
- `src/cli/interactive/components/{user_message,assistant_message,
  tool_execution,bash_execution,custom_message,compaction_summary,
  branch_summary,status}.py`：架构 line 959–971 表格逐行实现，全部继承
  W0 `tui.component.Component`、`render(width)` 输出超宽截断或抛
  `ComponentOverflowError`（与 ADR-0015 验收一致）。
- `src/cli/interactive/tool_renderer_registry.py`：
  - 入参用本地 dataclass `ToolRenderContext`（按 review P2 ⑤；不是
    pydantic 协议模型，受 acceptance §9 例外条款保护）：

    ```python
    @dataclass(frozen=True)
    class ToolRenderContext:
        tool_name: str
        tool_call_id: str
        args: Any                  # ToolExecutionStartEvent.args
        partial_result: Any | None # 来自最近一次 ToolExecutionUpdateEvent
        result: Any | None         # ToolExecutionEndEvent.result，未结束时为 None
        is_error: bool | None      # ToolExecutionEndEvent.is_error，未结束时为 None
        is_partial: bool           # ended_at is None
        started_at_ms: int         # ToolExecution component 在 start 时本地记录
        last_update_at_ms: int | None
        ended_at_ms: int | None    # component 在 end 时本地记录；duration = ended - started
    ```

  - 时间戳由 `ToolExecution` component 在收到 start / update / end event
    时本地记录（wall-clock ms），**不**消费 event 自带 timestamp，避免
    与 `ToolExecutionEndEvent` 的实际字段（`tool_name` / `result` /
    `is_error`，**无** `duration` / **无** `truncated`）漂移；
  - `ToolRendererRegistry.register(tool_name, renderer)` —— 注入式 API；
    `renderer: Callable[[ToolRenderContext, int], list[str]]`，第二个参数
    是 `width`；
  - M1 内置一条 generic renderer：tool 名 + 参数 JSON 摘要（截断 200
    字符）+ partial / final result 摘要（截断 200 字符）+ `is_error`
    标志 + 结束后展示 `duration_ms = ended_at_ms - started_at_ms`。
    M1 不承诺 `truncated`（不是 event 字段，由 M5 policy 在
    `result.metadata` 暴露后再加）；
  - **`src/tui` 与 `src/cli/interactive/components` 不允许 import 任何
    具体工具定义**；M5 在 `src/cli/tools/` 注册各 tool 自己的 renderer，
    保持工具知识不下沉到通用渲染层。
- `src/cli/interactive/event_router.py`：`EventRouter.route(event)`
  接收 `AgentSessionEvent` 或 `AssistantMessageEvent`。

`route` dispatch 表（discriminator 字段 `type`）：

- **`AgentSessionEvent` 路径**：
  - `agent_start` / `agent_end` / `turn_start` / `turn_end` → 状态机
    迁移（`idle → streaming → idle`），无可见组件；
  - `message_start` → 在消息列底新建对应 `Component`（按 message role
    选 `UserMessage` / `AssistantMessage` / `ToolResultMessage` /
    `BashExecutionMessage` / `CustomMessage` / `BranchSummary` /
    `CompactionSummary`），并标记为当前 active assistant target；
  - `message_update` → 把 payload `AssistantMessageEvent` 转发给当前
    active `AssistantMessage` 组件的 `apply(event)`，由组件累积 partial
    text / thinking / toolCall；
  - `message_end` → 组件标记完成，stopReason / usage / cost 写入 footer
    摘要；
  - `tool_execution_start` / `update` / `end` → 在对应 turn 下渲染
    `ToolExecution` 组件，组件本地累积时间戳 + partial_result，每次
    `request_render` 时构造 `ToolRenderContext` 调
    `ToolRendererRegistry.render(ctx, width)`；
  - `queue_update` / `compaction_start` / `compaction_end` /
    `auto_retry_start` / `auto_retry_end` → 顶部 status notification
    （限时显示 + 写入历史 log）。
- **`AssistantMessageEvent` top-level 路径**（M0 fixture
  `assistant_text_delta/events.jsonl` 等是裸 stream 帧，必须直接路由，
  不允许 harness 包装成 UI-only `message_update`）：
  - 进入时若没有 active `AssistantMessage` 组件，**lazy 创建**一个
    （等价于隐含一次 `message_start` for assistant role），并 attach
    到当前 turn；
  - `start` / `text_start` / `text_delta` / `text_end` /
    `thinking_start` / `thinking_delta` / `thinking_end` /
    `toolcall_start` / `toolcall_delta` / `toolcall_end` →
    转发给当前 `AssistantMessage.apply(event)`。**字面量按
    `src/ai_provider/types.py` 实现一致**（toolcall 是单词，无中划线，
    与 pi-mono `packages/ai/src/types.ts` 对齐）；写错 router 会漏
    fixture。
  - `done` → 等价 `message_end` 行为：组件标记完成，写 stopReason /
    usage / cost；clear active 引用；
  - `error` → 组件标记 error；clear active 引用；保留 partial 内容供
    后续 negative test 断言。
- 不允许 router 内识别任何不在两个 union 中的字段；遇到未知 event 抛
  `RuntimeError("contract violation: ...")`，与 architecture acceptance
  line 1163 一致。

W4 必须用 W5 deliverable 表列出的 7 条 M1 fixture（M0 已交付 3 条 +
M1 新增 4 条）全部 round-trip 一遍：每条 event 经过
`AgentSessionEventAdapter` / `AssistantMessageEventAdapter` 验证 →
`route` → 不抛错。这条作为 `tests/cli/interactive/test_event_router.py`
的 acceptance；其中 `assistant_text_delta` 与 `assistant_thinking_delta`
必须以 top-level `AssistantMessageEvent` 路径走通。其余 19 条 fixture
（M0 仅 expected.json 占位）不在 M1 router/playback 范围。

### W5. Mock playback harness

新增 `src/cli/interactive/playback.py`（与 event_router / app 同包，因为
harness 本质是 interactive layer 的 driver；`src/tui/` 内不放 harness）。

harness 持有 W4 `InteractiveController`，仅走它公开的两个面 ——
**event plane** `controller.dispatch_event(...)` 与 **control plane**
`controller.handle_abort()` / `inject_user_input(...)` /
`simulate_resize(...)` / `exit()`。harness 不能直接操纵 `tui.TUIApp`、
`EventRouter` 或任何 component 的内部状态。

`PlaybackHarness(events_path, sidecar_path, controller)`：

- 启动时一次性用 `AgentSessionEventAdapter` /
  `AssistantMessageEventAdapter` `validate_python` 全部 events，任何行
  失败立即抛错；
- 校验 sidecar `version == 1`；
- `delays_ms` 长度必须等于 events 行数，否则拒绝运行；
- 异步任务循环：
  1. `await asyncio.sleep(delays_ms[i] * speed_multiplier / 1000)`；
  2. **event plane**：`controller.dispatch_event(events[i])`；
  3. 处理所有 `inject.after_event_index == i`，全部走 **control plane**：
     - `abort` → `controller.handle_abort()`（M1 实现：把当前 active
       `AssistantMessage` / `ToolExecution` 标记 `aborted` + 状态机切回
       `idle` + editor 重新可输入；M3/M4 接入真实 `Agent.abort()` 后此
       入口语义不变）；
     - `user_input` → `controller.inject_user_input(inject.text)`；
     - `resize` → `controller.simulate_resize(width, height)`（不通过
       SIGWINCH，避免污染 process-level signal owner）；
     - `quit` → `controller.exit()`。
- 不向 fixture 目录写任何文件；播放过程 dump 走 `tmp/m1_playback_<ts>.log`。
- 入口：
  - `neomagi --playback tests/fixtures/pi_compat/assistant_text_delta`
    → 进入 TUI、playback 立即开始；
  - 交互内 `/play <fixture-name>` → selector overlay 列出
    `tests/fixtures/pi_compat/` 子目录、确认后启动 harness。
- 与 W2 editor 的关系：`--playback` 模式下提交 prompt 等于"播放下一段"或
  忽略（按 sidecar `injects` 中的 `user_input` 决定）；不会有真实模型回复。

**M1 必须新增的 fixture deliverable**（M0 仅交付 3 条 events.jsonl，按
review P2 ③ 把缺口列清，与 M1 PR 同 commit 提交，否则 W7 acceptance 会
依赖不存在的 fixture）：

| Fixture | 已有 | M1 必须新增 | 备注 |
| --- | --- | --- | --- |
| `assistant_text_delta` | events.jsonl + expected.json | — | 已就位 |
| `tool_execution_success` | events.jsonl + expected.json | — | 已就位 |
| `parallel_tools` | events.jsonl + expected.json | — | 已就位 |
| `assistant_thinking_delta` | expected.json | events.jsonl | 裸 `AssistantMessageEvent` thinking 流，覆盖 `thinking_start` / `thinking_delta` / `thinking_end` 路径 |
| `compaction` | expected.json | events.jsonl + playback.json（可空 inject） | 触发 `CompactionSummaryMessage` 渲染；events 含 `compaction_start` → `compaction_end` 包络 + `CompactionSummaryMessage` |
| `abort_during_stream` | （空） | events.jsonl + playback.json（含 `inject: abort`） | negative test：abort 后 partial 文本仍在、editor 复位 idle |
| `abort_during_tool` | （空） | events.jsonl + playback.json（含 `inject: abort`） | negative test：tool 行标记 `aborted`、editor 复位 idle |

新增的 4 条 fixture 都必须经过 `tests/test_fixture_round_trip.py` 既有
round-trip 断言（M0 已建），并新增到 `tests/cli/interactive/test_playback_
harness.py` 的 acceptance 列表。其余 18 条 fixture 不在 M1 范围（属 M2/M3
对应能力 PR）。

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

最低 acceptance 测试（前 4 项是 ADR-0015 §验收的硬要求）：

- `tests/tui/test_terminal.py`（W0 substrate）：
  - `TerminalSession` 进入 / 退出后 cooked mode 恢复、bracketed paste off、
    cursor 可见；
  - SIGINT / SIGTERM / 抛出 `RuntimeError` 仍恢复（`atexit` 兜底覆盖）。
- `tests/tui/test_stdin_buffer.py`（W0 substrate）：
  - partial ESC / CSI / OSC / APC 跨 read 续读不丢字节；
  - bracketed paste 包络识别为单个 `PasteEvent`；
  - 半个 escape sequence 不被当普通 key 转发。
- `tests/tui/test_renderer.py`（W0 substrate）：
  - first render 全量写；
  - 第二帧仅写 diff 行（断言写入字节数）；
  - resize / width change → full redraw；
  - content shrink → 旧尾行被显式 clear；
  - synchronized output `CSI ? 2026 h` / `l` 包裹批量写入。
- `tests/tui/test_width.py`（W0 substrate）：
  - CJK（"你好" → 4 列）、emoji（含 ZWJ 组合）、combining mark、ANSI SGR
    跳过、tab、截断、ANSI-aware wrap；
  - `Component.render(width)` 输出超宽 → 截断或抛
    `ComponentOverflowError`（negative case）。
- `tests/tui/test_lifecycle.py`：
  - 进入 / 退出回合可重入；
  - SIGINT / SIGTERM / 异常崩溃 → `stty -a` 检查 cooked mode 恢复；
  - bracketed paste off 验证（写入 `^[[200~test^[[201~` 后下一次写入仍是
    cooked）。
- `tests/tui/test_editor.py`：
  - Enter / Shift+Enter / Alt+Enter / Esc / 双 Esc 分别触发预期 action；
  - 中文输入（"你好"）caret 列号 = 4；
  - bracketed paste 多行 → 整段进入 buffer 不被逐字解释。
- `tests/cli/interactive/test_renderers.py`：
  - 8 类业务 component（含 status notification）分别用 fixture 数据 →
    render 结果 snapshot；
  - markdown 围栏代码块换行不超 viewport width；
  - `ToolRendererRegistry` 在未注册具体 tool 时回落 generic renderer，
    在注册后优先使用具体 renderer；M1 不允许测试 import `src/cli/tools/`。
- `tests/cli/interactive/test_event_router.py`：
  - W5 deliverable 表 7 条 M1 fixture 的每行 event → `route()` 不抛错；
  - 裸 `AssistantMessageEvent` 流（`assistant_text_delta` /
    `assistant_thinking_delta`）走 top-level path，lazy 创建
    `AssistantMessage` 组件；
  - 伪造的未知 `type` → 抛 `RuntimeError`。
- `tests/cli/interactive/test_playback_harness.py`（fixture 列表与 W5
  deliverable 表一一对应）：
  - `assistant_text_delta` 完整 playback → 最终 message 内容 = `Hello, world.`；
  - `assistant_thinking_delta`（M1 新增 events.jsonl）→ thinking 段累积
    并以 thinking content block 形式落到 `AssistantMessage`；
  - `tool_execution_success` → tool 行渲染包含 tool name + 结果摘要；
  - `parallel_tools` → 至少两个 tool 行同时出现在 streaming 状态；
  - `compaction`（M1 新增 events.jsonl + 可空 sidecar）→
    `CompactionSummaryMessage` 组件渲染并保留摘要文本；
  - `abort_during_stream`（M1 新增 events.jsonl + sidecar `inject:
    abort`）→ partial 文本仍可见、editor 重新 idle；
  - `abort_during_tool`（M1 新增 events.jsonl + sidecar `inject: abort`）
    → tool 行标记 `aborted`、editor 重新 idle。

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

`just md-doc-header` 必须对 M1 期间新增的 markdown（closeout、本 plan、
任何 amend 章节）应用。ADR-0015 已在 M1 启动前入库，无需再处理。

## 完成标准（Acceptance）

M1 视为完成需同时满足：

1. **Native ANSI substrate 就位**：`src/tui/{terminal,stdin_buffer,
   renderer,width}.py` 与 `Component` 抽象按 ADR-0015 §影响段实现，并通过
   `tests/tui/test_terminal.py` / `test_stdin_buffer.py` /
   `test_renderer.py` / `test_width.py` 全部 acceptance（含
   `Component.render(width)` 超宽 negative case）；`wcwidth` 列入
   `pyproject.toml`，`uv sync` 成功；`just lint` green、`complexity_guard`
   0 regression（如必要先 `just complexity-baseline`）。
2. **CLI 可启动**：`uv run neomagi`、`python -m cli` 两个入口都能进入 TUI；
   `uv run neomagi --help` 列出 `--playback` / `--print` / `--help`；
   `--print` 在 M1 仅返回 "not implemented in M1"（占位由 M9/M10 接入）。
3. **终端可恢复**：在 ADR-0015 锁定的 macOS Terminal / iTerm2 与 Ubuntu
   常见终端上，`Ctrl+C` / `/quit` / `kill <pid>` / 抛出 `SystemExit` /
   `raise RuntimeError` 五种退出路径全部恢复 cooked mode、显示光标、关闭
   bracketed paste（`tests/tui/test_lifecycle.py` green）。Windows 不在
   M1 验收范围。
4. **输入语义齐全**：editor 正确响应 Enter / Shift+Enter / Alt+Enter / Esc /
   双 Esc / `/` / `@` / `!` / `!!` / Ctrl+V / Tab；中文输入与 caret 列号
   匹配（`tests/tui/test_editor.py` green）。
5. **Renderer 套件全 green**：架构 line 959–971 表格的 8 行 renderer 全部
   实现并通过 snapshot 测试（`tests/cli/interactive/test_renderers.py`
   green）。
6. **Event router 契约**：`tests/cli/interactive/test_event_router.py`
   在 W5 deliverable 表列出的 7 条 M1 fixture 上 100% green；裸
   `AssistantMessageEvent` top-level path 走通（含 text 与 thinking 两条
   stream）；伪造的未知 `type` 触发 `RuntimeError`，确保 architecture
   acceptance line 1163 不会回退。
7. **Playback harness 可运行**：W5 deliverable 表列出的 7 条 M1 fixture
   （`assistant_text_delta` / `assistant_thinking_delta` /
   `tool_execution_success` / `parallel_tools` / `compaction` /
   `abort_during_stream` / `abort_during_tool`）全部播放成功；
   `abort_during_stream` 与 `abort_during_tool` 满足 negative test 要求
   （partial / error 状态保留 + editor 恢复 idle）。
8. **Slash command 占位完整**：autocomplete 列表覆盖 behavior matrix § A
   全部 21 条 Pi 内建命令（含 `[stub]` 标注）；`/new` / `/quit` /
   `/hotkeys` / `/play` 真正可执行。
9. **不引入 UI-only 协议**：`src/tui` 与 `src/cli/interactive` **禁止**
   定义任何 pydantic agent / session / message 模型；agent / session /
   message / event 协议类型只能从 `agent_core.types` /
   `cli.core.session_types` / `ai_provider.types` import。允许这两个包
   定义本地 dataclass 表达 substrate / interactive 内部 IPC（`KeyEvent`
   / `PasteEvent` / `ResizeEvent` / `CursorPosition` /
   `ToolRenderContext`），但这些 dataclass 不持久化、不进入
   `events.jsonl`、不暴露为公开协议。**`src/tui/app.py` 额外断言**：不
   import `agent_core.types` / `cli.core.session_types` /
   `ai_provider.types`（按 review P1 ①，agent-aware 接口归
   `cli.interactive.app.InteractiveController`）。
   `tests/cli/interactive/test_event_router.py` 包含静态扫描断言：
   `src/tui` 与 `src/cli/interactive` 不通过 `pydantic.BaseModel` 定义
   message / event 模型；`src/tui` 不出现协议类型 import。
10. **进度归档落地**：`dev_docs/progress/progress.md` 追加 closeout 条目；
    `dev_docs/logs/p1_m1_closeout.md` 与本 plan 一对一。

## 顺序与依赖

```
W0 (src/tui substrate: terminal / stdin_buffer / renderer / width / Component)
  ↓
W1 (src/cli/__main__ + src/tui/{app,lifecycle}) ──┬─→ W2 (src/tui editor / keymap / autocomplete)
                                                  ├─→ W3 (src/tui generic primitives: overlay / markdown / image) ──┐
                                                  └─→ W4 (src/cli/interactive: InteractiveController + components + event_router + ToolRendererRegistry) ──┤
                                                                                                                                    ├─→ W5 (src/cli/interactive/playback)
                                                                                                                                    │
                                                       W6 (src/cli/slash_commands) ────────────────────────────────────────────────┘
                                                                                                                                    ↓
                                                                                                                                  W7 (tests/tui/* + tests/cli/interactive/*)
                                                                                                                                    ↓
                                                                                                                                  W8 (closeout)
```

W0 是关键路径瓶颈，substrate 落地前 W1–W6 不能开工。W2/W3 可并行；W4
依赖 W3（generic primitives 与 `Component` 抽象必须先存在）；W5 依赖 W4
（playback 走 event/control plane，必须先有 router 与业务 component）；
W6 在 W2 之后即可启动；W7 收口（substrate 单测在 W0 推进过程中即可同步
落，不必等到 W7 末段）；W8 最后。

## 风险

- **Native ANSI runtime 成本**：W0 substrate 自写 terminal lifecycle、
  stdin buffer、key parser、diff renderer 与 width guard。M1 只支持 macOS
  Terminal / iTerm2 与 Ubuntu 常见终端，Windows 明确 deferred；W0 必须先
  全部 acceptance 通过再开 W1–W6。
- **中文 IME / 宽字符回退**：`wcwidth` 提供基础显示宽度；部分终端 combining
  mark / emoji 行为仍可能不同。M1 接受 "基本可用"，corner case 列入
  closeout deferred；M5/M9 视用户反馈再升级。
- **Bracketed paste 离场恢复**：异常退出时如果 `lifecycle.exit()` 没被调用，
  终端会卡在 paste 模式。`atexit` + 信号 + `try/finally` 三重兜底，并由
  `tests/tui/test_lifecycle.py` 强制覆盖五条退出路径。
- **Inline image 协议**：M1 一律 placeholder。任何"先做 Kitty / iTerm 简化
  实现"的诱惑都拒绝，避免 M2 引入真实图片时与 M1 实现冲突。
- **差分渲染闪烁**：W0 `Renderer` 已用 synchronized output 包裹批量写入；
  W3 generic primitives 与 W4 业务组件不得旁路 `Renderer.present` 直写
  终端，必须通过 `request_render` + `TUIApp` 主循环。如仍出现明显闪烁，
  closeout 中记录终端和 fixture，定位回 W0 substrate，避免在业务层临时
  补丁。
- **Slash command 与 extension 冲突**：M1 必须把 builtin registry 设计成
  M8 extension layer 的子集，否则 M8 重写工作量陡增；W6 实现时优先确认
  `RegisteredCommand` 字段对齐 `cli.extensions.types`。
- **Playback harness 与真实 runtime 的 path drift**：M4 真实接线时，路径
  不能因为 M1 的 mock 设计被锁死。`PlaybackHarness` 只能走
  `InteractiveController` 公开的两个面 —— event plane (`dispatch_event`)
  与 control plane (`handle_abort` / `inject_user_input` /
  `simulate_resize` / `exit`)，不直接操纵任何 component / router /
  `TUIApp` / substrate 内部状态。这条作为 W5 review checklist 的硬约束；
  M3/M4 接入真实 `Agent` 时把 event plane 的 source 从 harness 换成
  `Agent.events.subscribe()`，control plane 公开方法签名不变。
- **`complexity_guard` 抖动**：W3/W4 会引入大量新代码。建议 W3 启动前
  `just complexity-baseline` 在干净状态下刷一次；之后每条 PR 自查 ratchet。
- **依赖膨胀**：M1 只新增 `wcwidth`。`rich`、`rapidfuzz`、`syrupy` 等体验或
  测试便利依赖全部 deferred；若 W2/W3 中途想追加依赖，必须先回 ADR。

## 后续移交

M1 完成后立刻把以下 artifact 交给 M2–M4：

- `tui.terminal.TerminalSession` / `tui.stdin_buffer.StdinBuffer` /
  `tui.renderer.Renderer` / `tui.width.*` → M2/M3/M4/M5 共享的稳定
  substrate；任何更换或大改必须先回 ADR-0015 amend，再回此 plan / 后继
  milestone plan。
- `cli.interactive.event_router.EventRouter.route` → M3 真实 `AgentEvent`
  接入点；M4 把 event source 从 `PlaybackHarness` 切换到真实
  `Agent.events.subscribe()`。
- `tui.app.TUIApp` → generic substrate runtime（render / focus / input /
  resize / exit）；agent-aware 包装在 `cli.interactive.app.
  InteractiveController`。
- `cli.interactive.app.InteractiveController` 的 event plane
  (`dispatch_event`) + control plane (`handle_abort` / `inject_user_input`
  / `simulate_resize` / `exit`) → M4 唯一入口，禁止旁路；M3/M4 接入真实
  `Agent` 时把 event plane source 从 `PlaybackHarness` 换成
  `Agent.events.subscribe()`，control plane 公开方法签名不变。
- `cli.interactive.playback.PlaybackHarness` → M2 faux provider / M3
  agent loop 回归测试可继续复用，作为 cross-layer 字节级 contract 的
  根证据（architecture acceptance line 1163）。
- `cli.interactive.tool_renderer_registry.ToolRendererRegistry` → M5 注册
  各 built-in tool 的具体 renderer 入口；保持 `src/tui` 与
  `src/cli/interactive/components` 不感知工具定义。
- `cli.slash_commands.registry` → M8 extension API `registerCommand` 直接
  注册到同一 registry；M1 已保证 builtin / extension 命名空间隔离。
- ADR-0015 → 后续若有 inline image 协议或差分渲染升级，必须先回 ADR
  补充章节，再回本 plan / 后继 milestone plan。

Architecture / behavior matrix / playback format 文档在 M1 期间冻结；任何
对契约的修改必须先回 architecture，再回此 plan。
