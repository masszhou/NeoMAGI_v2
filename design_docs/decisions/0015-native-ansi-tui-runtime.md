---
doc_id: 019dc68e-60eb-710b-a688-9e45b0a98d65
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T23:12:00+02:00
---
# 0015-native-ansi-tui-runtime

- Status: accepted
- Date: 2026-04-25
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Related: `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md`
- Related: `design_docs/decisions/0013-python-async-for-pi-promise-extension-methods.md`
- Related: `design_docs/decisions/0014-extend-async-protocol-rule-to-extension-ui-context.md`
- Architecture: `design_docs/architecture/p1_pi_cli_technical_architecture.md` § TUI Contract / Open Design Questions

## 选了什么

- NeoMAGI P1-M1 TUI runtime 选择自研 native ANSI runtime，复刻 Pi TUI 的核心架构：
  - `Component.render(width) -> list[str]`
  - focused component `handle_input(data)`
  - terminal lifecycle：raw mode、bracketed paste、resize、cursor restore、drain input on exit
  - stdin buffer：partial ESC / CSI / OSC / APC sequence、bracketed paste 分包
  - renderer：line diff + synchronized output
  - overlay stack：focus、hide/show、anchor、size
  - editor：multi-line input、history、paste markers、autocomplete hook
- M1 支持范围限定为 macOS Terminal / iTerm2 与 Ubuntu 常见终端；Windows 不进入 M1 支持范围。
- 生产依赖只新增 `wcwidth`，作为 ANSI-aware width / slice / truncate / wrap 的基础。
- M1 不引入额外 formatter 依赖；Markdown / code block 先用极简 ANSI formatter 或 plain text fallback。

## 为什么

- Pi TUI 本身是自有 ANSI runtime，而不是基于 Ink / blessed / React-Ink 等框架。NeoMAGI 选择 Pi TUI 的重要原因，是 coding agent runtime 与 TUI 的细粒度交互：streaming message、tool execution、bash update、abort、queue、steer、follow-up、extension UI 都需要在同一套事件和组件模型下自然协作。
- NeoMAGI 的目标不是在第三方 Python TUI framework 上模拟 Pi，而是实现可长期扩展的 Pi-style TUI substrate。native ANSI runtime 保留 terminal lifecycle、input parsing、focus、overlay、diff render 的控制权。
- `wcwidth` 属于正确性依赖，不是体验增强依赖。Python `len()` / `textwrap` 无法可靠表达终端显示列宽；CJK、combining mark、emoji、ANSI sequence、IME cursor placement 都需要统一 width guard。
- M1 目标是可启动、可输入、可退出、可播放 fixture 的最小闭环。额外依赖会增加治理成本和 snapshot 漂移，不符合当前阶段的极简原则。

## 放弃了什么

- 方案 A：使用 `prompt_toolkit` 作为 TUI runtime。
  - 放弃原因：它会引入自己的 application lifecycle、layout、focus、key binding 和 buffer 抽象，长期可能与 Pi `Component` 契约、NeoMAGI `event_router` 边界、后续 coding-agent/TUI 丝滑交互目标冲突。
- 方案 B：使用 `textual`。
  - 放弃原因：DOM/CSS/widget 系统过重，定位更像 terminal desktop app，与 Pi-style component round-trip 不匹配。
- 方案 C：使用 `urwid`。
  - 放弃原因：async/lifecycle 集成需要额外适配层，且不贴近 Pi TUI 的 ANSI line renderer。
- 方案 D：在 M1 引入 `rich` 作为 Markdown / code block formatter。
  - 放弃原因：M1 不需要 formatter 质量作为闭环前提。`rich` 会增加依赖、snapshot 漂移和 formatter/runtime 边界治理成本；若未来 Markdown/code 展示成为真实瓶颈，再追加 ADR 评估离线 formatter。
- 方案 E：完整 inline image 协议。
  - 放弃原因：M1 先 placeholder，Kitty / iTerm / sixel 探测留接口，不阻塞 TUI skeleton。

## 影响

- `src/tui/terminal.py` 必须集中管理 raw mode、bracketed paste、cursor hide/show、resize handler、stdin drain 与退出恢复。业务代码不得直接操作这些 terminal lifecycle 状态。
- `TerminalSession.query_cursor_row()` 是低层 TTY helper：发 `\x1b[6n` DSR 查询并返回 `CursorQueryResult(row, leftover, attempted, fallback_allowed)`；非 TTY no-op 且 `fallback_allowed=False`，不写 stdout；不拥有 fallback anchor 计算、不接触 `Renderer` / `StdinBuffer`。
- `src/tui/stdin_buffer.py` 必须处理 partial ESC / CSI / OSC / APC sequence 与 bracketed paste；不能把半个 escape sequence 当普通输入转发。
- `src/tui/stdin_buffer.py` 必须丢弃 `CSI <digits>;<digits> R` cursor position report；这是 terminal response，不是用户输入，即使作为 late DSR response 进入 normal input path，也不能产生事件。
- `src/tui/renderer.py` 必须以 ANSI line model 为权威，并按 render mode 提供受控入口：`present()` 负责 anchored canvas frame，`present_live()` / `commit_lines()` / `clear_live_region()` 负责 command-mode live region 与 append-oriented scrollback。canvas first render 使用 `\x1b[<anchor>;1H` + `\x1b[J`，只清锚点以下；command mode 不做全屏 anchor，不清已提交 scrollback。所有 renderer 入口必须在行写入后 reset SGR；live/canvas frame rewrite 必须使用 synchronized output；cursor positioning 必须与对应 frame/live-region 更新在同一次 renderer 调用内完成。
- `src/tui/app.py` 的 `TUIApp._prepare_anchor()` 是 anchored renderer owner：在 `terminal.enter()` 后、第一次 render 前调用 DSR helper，把 leftover bytes 回灌 `self._stdin.feed()`，计算 bottom-reserved fallback，调用 `renderer.set_anchor()`，并用同一 anchor 计算 compose height。SIGWINCH 回调只标记 `_anchor_dirty`，下一次普通 loop tick 才重新 DSR。
- `src/tui/lifecycle.py` 退出路径在 termios 还原前调用 `Renderer.last_bottom_row()`；返回 `None` 时只做 terminal restore，未到屏底则移动到下一行 col=1，已到屏底则写 `\r\n` 滚动一行，保证 shell 新 prompt 起干净行。
- `src/tui/overlay.py` 中 spinner 帧推进与字符串来源归 `tui.components.spinner.Spinner`；overlay 只负责定位和焦点，不得自存 spinner 帧字符或推进逻辑。`tui.components.spinner.PI_FRAMES` 是 substrate 唯一 braille spinner 帧来源。
- `src/tui/components/` 是 substrate UI primitive 公开层，对应 pi-mono `packages/tui/src/components/` 分层；新增 substrate primitive 必须放这里，业务装配 / agent-aware 逻辑禁止下沉。该目录下文件只允许 import stdlib 和 `tui.*` 内部模块，不允许 `agent_core` / `cli.core` / `ai_provider`。
- `src/tui/width.py` 必须基于 `wcwidth` 提供 `visible_width`、`slice_by_columns`、`truncate_to_width`、ANSI-aware wrap 等能力。所有 `Component.render(width)` 输出必须经过 width guard；不得用 `len()` / `textwrap` 作为终端宽度真相。
- `event_router` 边界不变：W4 输入仍是 M0 的 pydantic union，不允许 terminal input event 或 formatter event 穿透为 UI-only agent protocol。
- M1 只接受 macOS / Ubuntu 终端兼容性；Windows 支持必须另起决策。
- 后续若要引入 `rich`、完整 inline image 协议、或替换 renderer，必须说明为什么 M1 native ANSI substrate 不够，并通过新 ADR 或 amend 记录取舍。

## 验收

- `wcwidth` 作为直接生产依赖进入 `pyproject.toml`，`uv sync` 成功。
- `TerminalSession` 在正常退出、Ctrl+C、SIGTERM、异常崩溃后恢复 cooked mode、关闭 bracketed paste、显示 cursor、移除 resize handler。
- stdin buffer 单测覆盖 partial ESC、CSI、OSC、APC、bracketed paste。
- renderer 单测覆盖 first render、line diff、resize full redraw、content shrink clear、synchronized output 包裹，以及 command-mode live region / committed transcript 的 SGR reset 与非全屏清理行为。
- width 单测覆盖 CJK、emoji、combining mark、ANSI color、tab、截断和 wrapping。
- `Component.render(width)` 输出超宽时必须截断或 fail-fast，测试覆盖 negative case。
- `just lint` green，`complexity_guard` 0 regression。
