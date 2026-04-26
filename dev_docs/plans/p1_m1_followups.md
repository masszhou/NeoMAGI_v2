---
doc_id: 019dca67-94ff-7238-a510-172ea02ca178
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-26T17:27:52+02:00
---
# P1-M1 Follow-up Implementation Plan: Pi-aligned UX increments

- Status: approved
- Date: 2026-04-26
- Roadmap: `design_docs/roadmap/p1_engine_pi.md` (§ P1-M1)
- Parent plan: `dev_docs/plans/p1_m1_tui_skeleton_and_mock_playback.md`（已 sign-off）
- Parent closeout: `dev_docs/logs/p1_m1_closeout.md`
- Pi-mono baseline: `97a38bf6` (ADR-0011)
- Governing decisions:
  - ADR-0011 Freeze pi-mono baseline at 97a38bf6
  - ADR-0015 Native ANSI TUI runtime —— 本 plan 涉及 substrate 层修改，需要
    amend ADR-0015 §影响段（具体见每个 W 项的"决策影响"小节）。
- Pi 行为参考：用户提供的 macOS Terminal 截图（`pi v0.69.0` 启动时 shell
  历史保留，TUI 区域从启动时光标行向下铺开）。

## 目标

在 P1-M1 sign-off 基础上，做几处贴近 Pi 真实交互观感的小幅追加，每条都是
独立可单独提交、可单独回滚的 substrate 或 interactive 层增量。所有改动
**不得**破坏 P1-M1 plan §完成标准里的任何一条 acceptance（10 条 + 评审三
轮 + 手测全程通过的状态），也**不得**影响 M2 startup 前置条件
（`InteractiveController` 公开 event/control plane 不动；`PlaybackHarness`
路径不动；fixture round-trip 不动）。

本 plan **不**做：

- 真实 provider / agent loop（M2/M3）
- inline image 真实终端协议（M2/M5）
- session manager / 历史导航（M6）
- 任何结构性架构改动

## 范围（追加项清单）

### W1. Anchored renderer：保留 shell 历史 + 退出留白

**Baseline 对齐前置（ADR-0011 防漂移）**：用户参考截图来自 `pi v0.69.0`，
比 ADR-0011 锁定的 baseline `97a38bf6` 新。Pi 的 anchored 渲染是其一贯
设计，但 plan 实施前**必须先在 pi-mono `packages/tui/src/tui.ts`
baseline `97a38bf6` 版本里确认 anchored 行为已经存在**，不是 v0.69.0
之后才加的。如确认 baseline 行为与本 W 设计不一致，调整为"实现 baseline
行为"，避免破坏 ADR-0011 的"以 97a38bf6 为唯一参照"承诺。

**问题陈述**：当前 `Renderer` 第一帧用 `\x1b[H\x1b[J` 全屏清，把启动 TUI
之前的 shell 历史完全擦掉；退出后 cursor 残留在 TUI 区域中部，shell 新
prompt 紧贴 TUI 残留输出。Pi 的做法是：进 TUI 时 shell 历史保留在锚点
之上，TUI 区域从启动时 cursor 行向下铺开；退出时 cursor 走到 TUI 区域之
下，shell prompt 起新行。截图证实：Pi v0.69.0 启动时 `$ pi` 之上的 `ls /
ls -a / ll` 输出全部保留，TUI 在它们下方铺开。

**实现要点**：

- Anchor ownership path 明确拆开，避免 `TerminalSession` 反向知道
  `Renderer` / `StdinBuffer`：
  - `TerminalSession.enter()` 仍只负责 raw mode / cursor / bracketed paste /
    keyboard protocol negotiation，不直接改 renderer anchor。
  - `TerminalSession.query_cursor_row(timeout_ms=100) -> CursorQueryResult`
    是低层 helper：在真实 TTY 上发 DSR 查询（`\x1b[6n`），从 stdin
    解析 `\x1b[<row>;<col>R`；返回 `CursorQueryResult(row: int | None,
    leftover: bytes, attempted: bool, fallback_allowed: bool)`。它不计算
    fallback anchor、不写入 `StdinBuffer`、不调用 `Renderer.set_anchor()`。
    真实 TTY timeout / 不支持 DSR 时返回 `row=None, attempted=True,
    fallback_allowed=True`；非 TTY 时必须 no-op，返回 `row=None,
    leftover=b"", attempted=False, fallback_allowed=False`，且不写任何换行。
  - `TUIApp._prepare_anchor(size)`（在 `terminal.enter()` 后、第一次 render
    前调用）拥有编排权：调用 `terminal.query_cursor_row()`，把
    `leftover` 用 `self._stdin.feed(leftover)` 回灌，再按
    `CursorQueryResult` 字段计算最终 `anchor_row`：`row is int` 时走
    DSR 成功路径；`row is None and fallback_allowed` 时走真实 TTY
    bottom-reserved fallback；`row is None and not fallback_allowed` 时保持
    anchor=1 且不写任何换行。最后调用 `self._renderer.set_anchor(anchor_row)`，
    并把同一值存到 `self._anchor_row` 供 `_compose_frame` 计算可用高度。
  - `reserved_height` 取 8（editor + status + 一些消息空间，可配置），并
    clamp 到当前 `terminal_rows` 范围内。
  - DSR 成功后先算 `available_height = terminal_rows - anchor_row + 1`；
    若 `available_height < reserved_height`，说明用户在屏幕底部附近启动
    TUI。此时写 `reserved_height - available_height` 个 `\n` 让终端滚动出
    最小工作区，再把 `anchor_row` 设为
    `terminal_rows - reserved_height + 1`（与 fallback 共用同一个
    `bottom_reserved_anchor()` helper）。这样 DSR 正常响应时也不会只剩
    一两行 TUI 可用空间。
  - 超时 100 ms 不响应 → 兜底方案：走同一个 `bottom_reserved_anchor()`
    helper；由于拿不到当前 cursor row，写 `terminal_rows` 个 `\n` 把光标
    保守推到屏底（会滚动当前 viewport，但保留 scrollback），然后返回
    `terminal_rows - reserved_height + 1`。
  - DSR timeout 后可能仍收到迟到的 cursor position report（CPR），形态为
    `\x1b[<row>;<col>R`。这类字节是 terminal response，不是用户输入：
    `TerminalSession.query_cursor_row()` 在查询窗口内要消费匹配的 CPR；
    查询窗口外的迟到 CPR 由 `StdinBuffer` 全局丢弃，不能产生 `KeyEvent` /
    `PasteEvent` / 普通字符事件。
  - 以上写换行 fallback **只允许在真实 TTY 且 DSR 超时/不支持时发生**；
    非 TTY / pipe / playback / subprocess smoke tests 不做 DSR、不写换行，
    anchor 保持默认 1，避免污染 stdout。
- `Renderer` 增 `anchor_row` 字段（默认 1，向后兼容）+ `set_anchor(row)`
  方法。所有 `_move_cursor(r, c)` 内部偏移成 `_move_cursor(r + anchor - 1, c)`。
  首帧不再走 `\x1b[H + \x1b[J`，改为 `_move_cursor(anchor, 1) + \x1b[J`
  —— `\x1b[J` 默认就是"清光标到屏幕末尾"，不会动锚点之上。
- `TUIApp._compose_frame` 把可用高度从 `self._rows` 改为
  `self._rows - anchor + 1`。`_RootComponent.render_with_height` 不动
  —— 它已经做高度感知组合了。
- `Renderer` 独立保存 `_last_presented_frame_height: int | None`：每次
  `present(frame)` 成功写出后设为 `len(frame)`；新建 renderer 和
  `Renderer.reset()` 后设回 `None`，避免 reset 后 lifecycle 读到旧行号。
  `last_bottom_row() -> int | None`：未成功 present 过第一帧或 reset 后返回
  `None`；否则返回 1-based 最后一行已绘制行号
  （`anchor + last_presented_frame_height - 1`）。`lifecycle.exit()` 在还原
  termios 之前统一调用该 helper：若返回 `None`，只做 terminal restore，
  不尝试按 TUI 底部移动 cursor；若 `last_bottom_row() < terminal_rows`，
  move cursor 到 `last_bottom_row() + 1, col=1` 并清当前行；若最后一帧已到
  屏底，move 到 `terminal_rows, col=1` 后写 `\r\n` 让终端滚动一行。不要
  在已经移动到空白行后再额外写 newline，避免 off-by-one 留白。
- Resize 时 `TerminalSession.install_resize_handler` 触发的回调运行在
  SIGWINCH 路径里，**不得**在回调内写 terminal、读 stdin 或等待 DSR。
  回调只做轻量动作：更新 rows/cols、`Renderer.reset()`、设置
  `self._anchor_dirty = True`、注入 `ResizeEvent` 并请求 render。下一次正常
  `TUIApp` loop tick 在 compose/present 前看到 `_anchor_dirty`，再调用
  `_prepare_anchor(size)` 重新 DSR；失败就走屏底 fallback。

**决策影响**（amend ADR-0015 §影响段，至少加这 5 条）：

1. `src/tui/renderer.py`：原文写"first render"隐含全屏 `\x1b[H\x1b[J`；
   amend 改为"first render = `\x1b[<anchor>;1H` + `\x1b[J`，仅清锚点以下；
   shell history 在锚点之上字节级保留，不动"。
2. `src/tui/terminal.py`：原 §影响段责任清单（raw mode / bracketed paste
   / SIGWINCH / cursor / drain）**没有 DSR**；amend 加一条
   "`TerminalSession.query_cursor_row()` 是低层 TTY helper：发 `\x1b[6n`
   DSR 查询并返回 `CursorQueryResult(row, leftover, attempted,
   fallback_allowed)`；非 TTY no-op 且 `fallback_allowed=False`，不写 stdout；
   不拥有 fallback anchor 计算、不接触 `Renderer` / `StdinBuffer`"。
3. `src/tui/app.py`：amend 加一条"`TUIApp._prepare_anchor()` 是 anchored
   renderer 的 owner：在 `terminal.enter()` 后、第一次 render 前调用 DSR
   helper，把 leftover bytes 回灌 `self._stdin.feed()`，计算 bottom-reserved
   fallback，调用 `renderer.set_anchor()`，并用同一个 anchor 计算 compose
   height"。
4. `src/tui/lifecycle.py`：amend 加一条"退出路径在 termios 还原前，调用
   `Renderer.last_bottom_row()`；返回 `None` 时只做 terminal restore；
   如果底部行未到屏底就移动到下一行 col=1，如果已经到屏底就写 `\r\n`
   滚动一行，shell 新 prompt 起干净行；不破坏 acceptance #3 的五条退出
   路径恢复保证"。
5. `src/tui/stdin_buffer.py`：amend 加一条"`CSI <digits>;<digits> R`
   cursor position report 是 terminal response，不是用户输入；即使作为
   late DSR response 在查询 timeout 后进入 normal input path，也必须被
   丢弃且不产生任何 event"。

**测试覆盖**：

- 单元：`Renderer.set_anchor(N)` 后所有 `present` 调用的 escape 序列含
  `(N + r - 1);c H` 而不是 `r;c H`（逻辑 row 是 1-based）。
- `tests/tui/test_terminal.py` 只测 `TerminalSession.query_cursor_row()` 的
  低层职责：DSR success 返回 raw row（例如 5）+ leftover bytes；timeout
  返回 `row=None, attempted=True, fallback_allowed=True` 且不写 fallback
  newlines；非 DSR bytes 被保存在 `leftover`；非 TTY no-op，返回
  `row=None, attempted=False, fallback_allowed=False`，stdout 不出现
  fallback newlines。
- `tests/tui/test_stdin_buffer.py` 增 late DSR response 用例：直接 feed
  `b"\x1b[12;34R"`（以及前后夹普通输入的变体）时，CPR 字节被丢弃，不
  产生 `KeyEvent`，周围真实用户输入仍正常解析。
- 集成：`TUIApp._prepare_anchor()` 用 fake terminal 返回 `row + leftover`
  时，断言 `renderer.set_anchor()` 收到最终 anchor、`self._anchor_row`
  同步更新、leftover bytes 进入 `StdinBuffer.feed()` 后仍能被正常解析。
- 集成：`TUIApp._prepare_anchor()` 覆盖 fallback 计算：raw row 足够时
  anchor=raw row；raw row 靠近屏底时（例如 `rows=10, reserved_height=8,
  row=9, fallback_allowed=True`）写 6 个 `\n` 并 anchor=3；真实 TTY
  timeout `row=None, fallback_allowed=True` 时写 `terminal_rows` 个 `\n`
  并 anchor=3；非 TTY / pipe 场景 `row=None, fallback_allowed=False`
  时保持 anchor=1 且 stdout 不出现 fallback newlines。
- 集成：resize callback 只设置 `_anchor_dirty` / 注入 `ResizeEvent` /
  request render，不调用 `query_cursor_row()`；下一次普通 loop tick 才调用
  `_prepare_anchor()`。
- 单元：`Renderer.last_bottom_row()` 覆盖未 present / reset 后返回 `None`
  以及 `anchor + last_presented_frame_height - 1` 的 off-by-one；lifecycle
  exit 测试分别覆盖"`None` 时不移动 cursor"、"未到屏底 move 到下一行"和
  "已到屏底写 `\r\n` 滚动"。
- 端到端：用 PTY 写已知数量行的"shell history"，进 TUI，验证锚点行被
  正确识别；退出 TUI，验证锚点之上的内容**字节级**未被改动。
- 手测：macOS Terminal / iTerm2 / gnome-terminal 至少各跑一遍 §2 全部
  退出路径（`/quit` `Ctrl+C` `kill -TERM` 异常崩溃）+ 验证退出后 shell
  history 完整保留。

**Acceptance**：

- 启动 `uv run python -m cli` 后，启动前的 shell 输出**字节级保留**在
  TUI 区域之上；退出后 shell prompt 出现在 TUI 区域之下的新行。
- 真实 TTY 上 DSR 查询失败或不支持时退化到屏底锚定，仍可正常使用，记录
  在 closeout 的"已知降级"段。
- 非 TTY / pipe / playback / subprocess smoke tests 下不做 DSR、不滚动、
  不写 fallback newlines，anchor 保持 1，避免污染 CLI stdout。
- DSR 查询成功但启动位置距离屏底不足 `reserved_height` 时，也退化到
  bottom-reserved anchor；不能出现只有 1–2 行可用高度的压缩 TUI。
- DSR timeout 后迟到的 `CSI <row>;<col> R` cursor position report 不会
  进入用户输入流，不产生 `KeyEvent`。
- `tests/tui/test_renderer.py` + `tests/tui/test_terminal.py` 增 ~5 条
  用例覆盖 anchor 偏移逻辑。
- `pytest tests/` 仍 180+ 用例 green；`just lint` green；
  `complexity_guard regressions=0`。

**Resize setting 注意**：不要把本 W 的 resize re-anchor 接到
`terminal.clearOnShrink`。pi-mono baseline 里的 `clearOnShrink` 控制的是
内容变短时是否清空残留行 / resize redraw 行为，不是"保留旧 anchor"开关。
本 W 只定义固定策略：真实 TTY resize 后重新 DSR；失败走屏底锚定；非 TTY
保持 anchor=1。如果未来需要"resize 时保留旧 anchor"之类策略，另开 setting
和 ADR，不复用 `clearOnShrink`。

---

### W2. Spinner primitive：通用等待动画 + 现有 Loader 收敛

**问题陈述**：现状的 `Loader` / `CancellableLoader` 在 `src/tui/overlay.py`
里继承自 `Overlay`，把"动画帧状态"和"浮层定位 + 焦点"耦在一起。后续要
让 `ToolExecutionComponent` 的状态行（例如 `running 2.3s ⠋`）、
`compaction` banner、`auto_retry` 进度条等复用同一个 spinner 时，没有
非-overlay 入口可用，迫使每个业务组件都自己 reinvent 帧推进逻辑 + 帧
序列字符串，最终 Pi-compatible 的 frames 会漂移。

**实现要点**：

- 新增 `src/tui/components/spinner.py`（与 §W3 共用 `tui/components/`
  目录；和 pi-mono `packages/tui/src/components/` 分层对齐）：
  - `Spinner` 类，纯 `tui.component.Component` 子类（**不**继承
    `Overlay`）；构造参数 `label: str`、`frames: Sequence[str] = PI_FRAMES`、
    `tick_interval: float = 0.08`、`style: Callable[[str], str] | None =
    None`、`clock: Callable[[], float] | None = None`。
  - `Spinner.tick()`：如果 `self._frames` 为空，直接 no-op，不
    `request_render()`、不做模运算、不 schedule 下一次 wake；否则才执行
    `self._frame = (self._frame + 1) % len(self._frames)`，然后
    `self.request_render()`，并在 auto-tick scheduler 存在时排下一次
    callback。**绝不**直接写 terminal。
  - `Spinner.render(width)`：frames 非空时渲染最终文本
    `f"{current_frame} {label}"`；frames 为空时渲染 `label` 本身，不加前导
    空格。颜色 / 样式不在 Spinner 里硬编码，由调用者通过
    `style: Callable[[str], str] | None = None` 注入；style 作用于上述最终
    文本，再经 `pad_to_width` 截断/补齐到 `width` 并作为单行返回。
  - 模块顶常量 `PI_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸",
    "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")`（和 pi-mono `packages/tui/src/`
    对齐）；其他模块**不允许**再定义自己的 spinner frames。
  - `Spinner.set_label(text)` / `Spinner.set_frames(seq)` 允许运行时
    更新（compaction 进度文案要刷新）。`set_frames(seq)` 每次都复制输入
    序列并把 `_frame = 0`，避免旧 frame index 在切到短序列时越界或显示
    不确定帧；如果新 frames 非空且 auto-tick scheduler 已 attach，则排
    第一帧；如果新 frames 为空，不排队，已在队列里的旧 callback 到期时按
    frames-empty `tick()` no-op 处理。
  - **`set_frames([])` 进入 no-indicator 状态**（直接对接架构 line 794
    `setWorkingIndicator({frames: []})` extension API 契约）：只隐藏
    spinner indicator，不隐藏工作文案。`render(width)` 仍返回 label/message
    行（无 braille frame 前缀，经 `pad_to_width` 截断/补齐）；`tick()`
    no-op，不 request render、不做模运算（避免 `len(frames) == 0` 触发
    `ZeroDivisionError`）、不再调度下次 wake；`set_frames(non_empty)`
    后 `_frame` 重置为 0 并恢复正常。这样和
    pi-mono loader 的"message 仍显示、只省略 frame"语义一致，M8 接
    extension API 时不需要再为这个特殊值打 patch。
- Auto-tick 集成（**需要扩展** `TUIApp`，现有 `schedule_wake(when)` 不够）：
  - **现有 `schedule_wake(when)` 的语义**：到期只把 `_render_requested`
    置 True，触发一次重画 —— 对 status notification TTL 的"过期淘汰"
    场景够用（`_alive_notifications` 在 render 时自然过滤）。但对
    spinner 不够：到期重画的是**同一帧**，动画不会自动转。如果只接到
    `schedule_wake`，结果是终端定时刷一下，画面纹丝不动。
  - **W2 扩展**：`TUIApp.schedule_callback(when: float, fn:
    Callable[[], None]) -> None`。`_check_wakeups()` 在内部把到期的
    callback 取出来**先执行 `fn()` 再 set `_render_requested = True`**。
    `schedule_wake(when)` 保留为 `schedule_callback(when, lambda: None)`
    的便捷别名（回调=空操作，行为等价于现状）。
  - `Spinner` 构造时接收可选 `clock: Callable[[], float]`（默认
    `time.monotonic`），只用于 auto-tick 调度测试；手动 `tick()` 不依赖
    外部时钟。
  - `Spinner.attach_tick_scheduler(scheduler: Callable[[float,
    Callable[[], None]], None] | None)`：保存一个 `schedule_callback`
    形态的回调。传入非空 scheduler 且当前 frames 非空时，**立即
    schedule 第一帧** `scheduler(clock() + tick_interval, self.tick)`，但不
    立刻推进 `_frame`；之后每次 `tick()` 仅在 frames 非空时 schedule 下一次
    `scheduler(clock() + tick_interval, self.tick)`。这定义了 auto-tick 的
    启动语义：owner 只需要 attach scheduler，不需要先手动 `tick()`。如果
    attach 时 frames 为空，只保存 scheduler 不排队；后续
    `set_frames(non_empty)` 且 scheduler 仍存在时再排第一帧。
    `attach_tick_scheduler(None)` 关闭自动 tick；外部 owner 仍可手动
    `tick()`，但手动 tick 不会在 scheduler 已清空或 frames 为空时继续排队。
  - 测试三层：(1) `TUIApp.schedule_callback` 单测：到期 callback 被调用
    且 `_render_requested` 为 True；(2) `Spinner` 单测：用 fake scheduler
    验证 frames 非空时 attach 会 schedule 首次回调、`tick()` 末尾会
    schedule 下一次回调且回调指向 `self.tick`；frames 为空时 attach 和
    tick 都不排队；
    (3) 集成：`Spinner` + `TUIApp` 跑一段时间（注入 fake clock + 手动驱
    动 `_check_wakeups`），断言连续 N tick 后 `_frame` 推进了 N 次。
  - 生产由 `InteractiveController.bootstrap()` 把 `app.schedule_callback`
    传给业务层创建的 `Spinner` 实例（具体哪个 owner 创建在 M2/M3/M5
    时再定，本 W 只交付 substrate 接口）。
- `src/tui/overlay.py` 收敛：
  - `Loader(label, frames=None)` 改成"持有一个 `Spinner` 实例 + 把它
    渲染在 Overlay 的 body 里"，不再自己存帧或字符串。`tick()` 转发到
    `self._spinner.tick()`。**保留**类名和外部签名以避免连锁修改
    `cli.interactive` 层。
  - `CancellableLoader(label, on_cancel)` 同上 + Esc handler，body 末尾
    追加 `(Esc to cancel)` 提示行。
  - 删除 `Loader._FRAMES` 字面量；改 import `tui.components.spinner.PI_FRAMES`。
- `cli.interactive.tool_renderer_registry.generic_tool_renderer` 和未来
  business-side 用法**不**在本 W 范围 —— 它们在 M2/M3/M5 接进真 runtime
  时再决定怎么把 `Spinner` 嵌进自己的 render（最常见会是把 `Spinner`
  作为 `ToolExecutionComponent` 的子组件，`tick()` 由 controller 在收到
  `tool_execution_update` 时手动驱动，或者通过 auto-tick scheduler
  （`schedule_callback`）自动跑）。本 W 只交付 substrate primitive +
  收敛现有重复实现。

**决策影响**（amend ADR-0015 §影响段，至少加这 2 条）：

1. `src/tui/overlay.py`：amend 写明"spinner 帧推进 + 字符串归
   `tui.components.spinner.Spinner`；overlay 仅负责定位和焦点，**不得**
   自存帧字符或推进逻辑"。原 §影响段对 overlay 没有这条边界，amend
   后业务层也跟随生效。
2. **`tui.components.spinner.PI_FRAMES` 是 substrate 唯一 spinner 字符
   来源**（与 pi-mono `packages/tui/src/components/loader.ts` 帧序列
   对齐，按 ADR-0011 baseline `97a38bf6`）。amend 加约束："任何模块
   不得自定义 braille spinner 帧序列；`tui.components.spinner.PI_FRAMES`
   是唯一来源"。
   - **静态扫描断言（落到 W2 测试里）**：原计划写"grep `⠋⠙⠹` 只命中
     spinner.py"是错的 —— `PI_FRAMES` 用 tuple 字面量逐字符 quote
     （`("⠋", "⠙", "⠹", ...)`），文件里**不会**含连续子串 `⠋⠙⠹`，
     既漏 spinner.py 自己也漏可能的新重复（比如别处写 `_FRAMES =
     "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"` 单字符串字面量的写法都能逃过去）。改双层覆盖：
     - **AST 级**：walk `src/**/*.py`，找 module-level `Assign` 节点，
       右值若是 `Tuple` / `List` / `Constant(str)` 且包含 ≥ 5 个 braille
       glyph（U+2800–U+28FF）或长度 ≥ 5 的连续 braille 字符串字面量，
       目标必须是 `tui/components/spinner.py` 里的 `PI_FRAMES`，否则
       fail。这同时拦截 tuple 写法和单字符串写法。
     - **单 glyph 兜底**：`grep -l "⠋"` `src/**/*.py` 命中文件集合必须
       仅含 `src/tui/components/spinner.py`。简单粗暴但和 AST 互补，AST
       误判时也能挂。

**测试覆盖**：

- `tests/tui/test_spinner.py` 新增：
  - 默认 `PI_FRAMES` 长度 = 10、首帧 = `⠋`、tick 后顺序推进、
    `len(frames)` 次后回到首帧。
  - `set_label("compacting…")` 后 `render(width)` 含新 label。
  - `render(width=4)` 截断到 4 列、宽度 0 返回 `[""]`。
  - `style=lambda s: f"\x1b[33m{s}\x1b[0m"` 注入后输出含 `\x1b[33m`。
  - `set_frames([])` 进入 no-indicator：`render(width)` 仍返回 label/message
    行但不含任何 braille frame，且文本不以空格开头；`tick()` 不抛
    `ZeroDivisionError`、不调 attached scheduler；后续
    `set_frames(non_empty)` 恢复正常，`_frame` 重置为 0，且 scheduler 已
    attach 时会排第一帧。
  - `set_frames(seq)` 每次复制并重置 frame index：从默认 10 帧 tick 到
    `_frame=9` 后切到 2 帧自定义序列，下一次 render 显示新序列第 0 帧；
    从 `frames=[]` 切回非空 frames 时同样从第 0 帧开始。
  - `attach_tick_scheduler(fake)` 后立即调用 `fake(now+interval,
    self.tick)` 一次且不推进 `_frame`；执行该回调后 `_frame` 推进一次并
    再 schedule 下一次；frames 为空时 attach 不排队；不 attach 时
    `tick()` 不调任何 scheduler。
- `tests/tui/test_app.py`（或 `test_renderer.py`）补 `TUIApp.schedule_callback`：
  到期回调被调用 + `_render_requested` 置 True；同时验证现有
  `schedule_wake(when)` 行为兼容（callback=空操作）。
- `tests/tui/test_spinner.py` 集成测试：注入 fake clock，连续触发 N 次
  `_check_wakeups` due，断言 `Spinner._frame` 推进了 N 次（防止
  "scheduler 接通但 callback 没真转 frame" 这类回归）。
- `tests/tui/test_overlay.py` 增 1–2 条：`Loader` / `CancellableLoader`
  组合到 `Spinner` 后行为不变（同样的 frames、同样的 tick 行为、同样的
  Esc 关闭）。
- **静态扫描断言**（落到 `tests/tui/test_spinner.py` 或独立
  `tests/tui/test_substrate_invariants.py`）：
  - AST walk `src/`：找带 ≥ 5 个 braille glyph（U+2800–U+28FF）的字符串
    或 tuple/list 字面量，目标必须只在 `src/tui/components/spinner.py`
    的 `PI_FRAMES` 赋值里。
  - 单 glyph grep 兜底：`Path("src").rglob("*.py")` 中文件含字符 `⠋`
    的集合 == `{"src/tui/components/spinner.py"}`。
  - 新写"自定义 spinner frames" 时两条扫描至少一条会挂。
- `pytest tests/` 不引入新 fail；`just lint` green；
  `complexity_guard regressions=0`。

**Acceptance**：

- `src/tui/components/spinner.py` 落地，`PI_FRAMES` 是 substrate 唯一
  帧来源；测试覆盖里"AST 级 + 单 glyph"双扫描断言锁定（替代原计划那
  个不会命中 tuple 字面量的 `"⠋⠙⠹"` 子串 grep）。
- `TUIApp.schedule_callback(when, fn)` 落地，到期先 `fn()` 再请求重画；
  现有 `schedule_wake(when)` 退化为 `schedule_callback(when, lambda:
  None)`，status notification TTL 路径无任何破坏。
- `Spinner` 接 `attach_tick_scheduler` 后会先排首个 callback；到期
  callback 真的会推进 `_frame`（集成测试断言连续 N 次 due → frame
  推进 N 次）。
- `Spinner.set_frames([])` 只隐藏 indicator，不隐藏 label/message；测试
  断言 render 仍输出工作文案、不含 braille、不以空格开头，且不会继续
  自动 tick。
- `Spinner.set_frames(seq)` 每次重置 `_frame=0`；从 10 帧切到 2 帧、从空
  frames 切回非空 frames 都有测试覆盖。
- `Loader` / `CancellableLoader` 内部改用 `Spinner`，外部签名不变。
- `cli.interactive` 层无任何破坏（既有 `_open_quit_confirm` 等路径保持
  绿测）。
- 新增 `tests/tui/test_spinner.py`，5 条以上单测。
- 业务接入由 M2/M3/M5 在各自里程碑做，本 W 的 closeout 段明确写"primitive
  ready, no business wiring"。

---

### W3. 补齐 substrate UI primitives：Text / Spacer / Box / Container / TruncatedText

**问题陈述**：当前 `src/tui/` 只有 `Component` 抽象 + `Editor` /
`Overlay` / `Loader` 等具名复合件，缺少 pi-mono `packages/tui/src/components/`
那一层"通用、可组合、不带业务语义"的小积木：`Text` / `Spacer` / `Box`
/ `Container` / `TruncatedText`。结果就是 `_RootComponent` 和
`MessageListComponent` 这种本应是 substrate-level 容器的东西被迫挂在
`src/cli/interactive/`，做了一半的容器职责（顺序拼接子组件 + propagate
`request_render`），用法被锁死在 "agent message + status + editor" 这一
种装配上，未来要做"version banner / skill list / update notice"等任何
新装配都要再造一遍轮子。

Pi-mono 对应位置（baseline `97a38bf6`）：

| primitive | Pi-mono 路径 |
| --- | --- |
| `Component` / `Focusable` / `Container` / `TUI` | `packages/tui/src/tui.ts:17`, `tui.ts:178` |
| `Text` | `packages/tui/src/components/text.ts:7` |
| `Spacer` | `packages/tui/src/components/spacer.ts:6` |
| `Box` | `packages/tui/src/components/box.ts:14` |
| `Loader` | `packages/tui/src/components/loader.ts:17`（§W2 已收敛到 `Spinner`） |
| `CancellableLoader` | `packages/tui/src/components/cancellable-loader.ts:13`（§W2 同上） |
| `TruncatedText` | `packages/tui/src/components/truncated-text.ts:7` |

全部从 `packages/tui/src/index.ts:12` 顶层导出 —— pi-mono 的 substrate
公开面就是这些。

**实现要点**：

新增五个文件到 `src/tui/components/`（与 §W2 `spinner.py` 同目录）。
所有 primitive 都是 `tui.component.Component` 的纯子类，不引入新的
substrate 概念（不碰 `Renderer` / `TerminalSession` / `TUIApp`），不
import 任何 `agent_core` / `cli.core` / `ai_provider` —— 静态扫描
`tests/cli/interactive/test_event_router.py::test_src_tui_does_not_import_protocol_modules`
覆盖。

- `src/tui/components/text.py` —— `Text(content: str, *, style:
  Callable[[str], str] | None = None)`：单段文本（支持内嵌 `\n`），
  `render(width)` 走 `tui.width.wrap_to_width(content, width)`，每行可选
  套 `style` 注入 ANSI。**不**做 markdown 解析（那是 `tui/markdown.py`
  的事）。空字符串渲染为单行空白（与 `wrap_to_width` 现有约定一致）。
- `src/tui/components/spacer.py` —— `Spacer(rows: int = 1)`：
  `render(width)` 返回 `[" " * width] * rows`。仅用于布局留白；
  `rows=0` 返回空列表。
- `src/tui/components/box.py` —— `Box(child: Component, *, padding:
  int = 0, border: bool = False, border_style: Callable[[str], str] |
  None = None)`：包一个子组件，内边距 `padding` 列 / 行；`border=True`
  时画 `┌─┐ │ │ └─┘` 边框，宽度通过 `tui.width.visible_width` 计算
  避免 CJK 错位。子组件 `request_render` propagate 到 `Box.attach` 注入
  的回调。
- `src/tui/components/container.py` —— `Container(*, direction:
  Literal["vertical"] = "vertical")`：管理一个有序子列表，`render(width)`
  按方向拼接子组件输出。本 W 只做 `vertical`（`horizontal` 留 M-later，
  没用例触发）。提供 `append(child)` / `clear()` / `children` 与现有
  `MessageListComponent` 同形态接口，便于 §W3 第二阶段直接替换。
  `attach(callback)` propagate 到所有子组件，`detach()` 同样。
- `src/tui/components/truncated_text.py` —— `TruncatedText(content:
  str, *, ellipsis: str = "…", style: Callable[[str], str] | None =
  None)`：单行文本，`render(width)` 用 `tui.width.truncate_to_width(content,
  width, ellipsis=ellipsis)` 截断，永不换行（这是和 `Text` 的关键差异：
  `Text` 走 wrap、`TruncatedText` 走 truncate）。

**第二阶段（可选 / 可拆分独立提交）**：把 `cli.interactive` 现有的
两个容器迁到新 substrate primitive。**不**改公开 API：

- `_RootComponent` 改为持有一个 `Container` + 高度感知组合逻辑保留
  在外层（因为 `render_with_height` 是它独有的语义，`Container` 不应
  耦入 height-aware clipping）。或者更好：把 `render_with_height` 也
  上移到 `Container` 的可选行为里（`pinned_top` / `pinned_bottom` /
  `scrollable_middle` 子组件标记），但这步**不**在本 W 范围内做，需要
  独立 ADR amend 和 plan 条目。本 W 仅做"`_RootComponent` 内部用
  `Container` 拼接，保留 `render_with_height` 在原位"。
- `MessageListComponent` 改为 `Container` 的薄壳（保留类名 + 公开
  方法签名）。Tests `test_renderers.py` / `test_event_router.py` 不动。

第二阶段做完后，`src/cli/interactive/components/` 只剩"业务消息组件"
（`AssistantMessageComponent` 等 9 个），不再有"容器 + 布局"职责。

**决策影响**（amend ADR-0015 §影响段 + 架构文档同步，至少加这 3 条）：

1. ADR-0015 §影响段新增一节："`src/tui/components/` 为 substrate UI
   primitive 公开层，对应 pi-mono `packages/tui/src/components/` 分层；
   任何新增 substrate primitive 必须放这里，业务装配 / agent-aware
   逻辑禁止下沉。该目录下文件只允许 import stdlib 和 `tui.*` 内部模块；
   不允许 `agent_core` / `cli.core` / `ai_provider`。"
2. 同步把本 W 落地的 5 个 primitive（`Text` / `Spacer` / `Box` /
   `Container` / `TruncatedText`）补进**架构文档 line 957**的 M1
   substrate components 列表 —— 原列表（"markdown renderer, inline image
   renderer, select list, settings list, loaders"）是 M1 最小集，不是
   封闭枚举；扩展后让"M1 substrate primitive 公开面"完整化，避免后续
   读架构文档时漏掉。
3. ADR-0015 §影响 `Component.render(width)` 段保持不变；W3 所有 primitive
   都遵守该契约（`render(width) -> list[str]`、超宽截断或 fail-fast、
   走 `tui.width` 计算列宽），无需 amend 这条。

**测试覆盖**：

- `tests/tui/components/test_text.py`：wrap 行为、宽字符、空串、style
  注入。
- `tests/tui/components/test_spacer.py`：默认 1 行、`rows=0` 返回空、
  width=0 边界。
- `tests/tui/components/test_box.py`：padding 正确、border 在 CJK 内容
  下不错位、子组件 `request_render` propagate。
- `tests/tui/components/test_container.py`：`append` / `clear` / 顺序、
  子组件 `request_render` propagate、空容器 render 返回 `[]`。
- `tests/tui/components/test_truncated_text.py`：宽度足够时不截断、
  超宽时带 ellipsis、CJK 边界（"你好世界" 截到 6 列应得 "你好…"
  或 "你好世…"，按 `truncate_to_width` 现有契约）。
- 静态扫描：`grep -r "import" src/tui/components/` 仅命中 stdlib +
  `tui.{component,width,...}` 内部模块 —— 不允许 `agent_core` /
  `cli.core` / `ai_provider`。
- **既有 acceptance #9 静态扫描覆盖范围确认**：M1 plan §完成标准 #9 由
  `tests/cli/interactive/test_event_router.py::test_src_tui_does_not_import_protocol_modules`
  + `::test_neither_tui_nor_interactive_define_pydantic_models` 实现。
  W3 落地前先 `git grep "rglob\|walk_py" tests/cli/interactive/test_event_router.py`
  确认这两个静态扫描**递归**到 `src/tui/**/*.py`（含 `components/`
  子目录）；如发现是平铺扫描（仅顶层 `.py`），W3 同 PR 内修正为
  `Path("src/tui").rglob("*.py")`，否则新 primitive 会成为 acceptance
  #9 的隐性绕道。
- 第二阶段：`tests/cli/interactive/test_renderers.py` /
  `test_event_router.py` / `test_controller_regressions.py` 全部继续
  绿测，证明迁移没破坏现有装配。

**Acceptance**：

- `src/tui/components/{text,spacer,box,container,truncated_text}.py`
  落地，每个文件 ≤ 80 行（substrate primitive 本就该薄），通过
  `complexity_guard`。
- 新增 5 个测试文件 ~25 用例。
- `tests/cli/interactive/test_event_router.py` 的 substrate 静态扫描
  覆盖到 `src/tui/components/` 子目录（保证业务类型不被偷偷 import
  进 substrate）。
- 第二阶段（如果同 W 完成）：`MessageListComponent` 是 `Container`
  的薄壳，`_RootComponent` 内部用 `Container` 拼接 status / messages
  / editor，原 `render_with_height` + `editor_offset` 接口签名不变。
- `pytest tests/` 仍 200+ 用例 green；`just lint` green；
  `complexity_guard regressions=0`。

---

### Wn. _（待你追加更多）_

---

## 顺序与依赖

```
W1 (anchored renderer — substrate 改动)
  ↓
W2 / W3 / Wn （按你后续追加内容定）
  ↓
封板：closeout 追加段 + progress 单条 sign-off
```

W1 是 substrate 改动，建议优先做完并独立提交，之后的小点再分别基于稳定
substrate 跑。如果某条追加项依赖 W1 之外的子集，在该条的实现要点里明确
写出。

## 风险

- **DSR 查询跨终端兼容性**：macOS Terminal / iTerm2 / Linux 主流终端都
  支持，但 SSH 嵌入 / tmux 嵌套 / 早期 Windows 终端可能不支持或回响应
  时序异常。Mitigation：真实 TTY 上 100 ms 超时 + 兜底"屏底锚定"；非
  TTY no-op，避免污染 stdout；并把降级体验记录在 closeout。
- **Resize 后 anchor 失效**：终端 resize 时已渲染的内容会被重排，原来的
  anchor 行可能已经不指向 TUI 顶部。Mitigation：SIGWINCH callback 只做
  `Renderer.reset()`、标记 `_anchor_dirty`、注入 `ResizeEvent` 并请求
  render；下一次正常 loop tick 再调用 `_prepare_anchor()` 重新 DSR，失败
  就重新走 fallback。禁止在 signal handler 直连路径里做 terminal I/O 或
  100 ms 等待。
- **退出留白行计算错误导致 shell prompt 覆盖 TUI 残留**：`lifecycle.exit()`
  必须基于 1-based `Renderer.last_bottom_row()` 放置 cursor：未 present
  过或 reset 后返回 `None` 时只恢复 terminal；未到屏底时移到
  `last_bottom_row() + 1`，已到屏底时写 `\r\n` 滚动一行；否则 shell 新
  prompt 可能盖在 TUI 最后一帧上。Mitigation：renderer 独立保存
  `_last_presented_frame_height`，只暴露 helper 给 lifecycle 用，避免重复
  计算和 reset 后读旧状态。
- **第一次 render 之前的字节延迟**：DSR 握手期间 stdin 必须暂存任何
  user 首键；否则用户在终端启动瞬间敲的字符可能丢。Mitigation：
  `TerminalSession.query_cursor_row()` 返回非 DSR leftover bytes，由
  `TUIApp._prepare_anchor()` 回灌给 `self._stdin.feed()`。

## 后续移交

每条追加项做完后：

- 更新 `dev_docs/logs/p1_m1_closeout.md`（按现有"手测追加"段落格式追加
  新段，描述根因 / 修法 / acceptance 影响）。
- 在 `dev_docs/progress/progress.md` 末尾追加单条 sign-off 条目（按
  Status / Done / Evidence / Next / Risk 模板）。
- 如有 ADR amend，更新 `design_docs/decisions/INDEX.md` 指向 amend 段。
- 如修改了用户可见行为，同步更新
  `dev_docs/user_tests/p1_m1_manual_test_plan.md` 对应章节的期望和失败
  模式判定。
