---
doc_id: 019dc6d2-7d14-74d8-a3f3-66d5ca3820ad
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-26T00:46:10+02:00
---
# P1-M1 Closeout

- Status: done
- Date: 2026-04-26
- Plan: `dev_docs/plans/p1_m1_tui_skeleton_and_mock_playback.md`
- Baseline: pi-mono `97a38bf6` (ADR-0011)
- Governing decisions: ADR-0009, ADR-0010, ADR-0013, ADR-0014, ADR-0015
- 验收来源：plan §完成标准（acceptance）

## W0-W8 状态

| W | 工作项 | 状态 | 关键产出 |
| --- | --- | --- | --- |
| W0 | Native ANSI substrate（落实 ADR-0015） | done | `src/tui/{terminal,stdin_buffer,renderer,width,component}.py`；`wcwidth>=0.2.13` 进入生产依赖；`Component.render(width)` 行宽守护 + `ComponentOverflowError`；`Renderer.present(frame, cursor=...)` 单入口 + line-diff + synchronized output；`StdinBuffer` 处理 partial ESC / CSI / OSC / APC + bracketed paste + xterm modifyOtherKeys / Kitty CSI-u；`TerminalSession` SIGWINCH 单 owner + raw mode + bracketed paste 包裹 + best-effort keyboard-protocol 探测 |
| W1 | CLI 入口 + 终端 lifecycle | done | `src/cli/__main__.py` + `pyproject.toml [project.scripts] neomagi`；`src/tui/app.py` `TUIApp`（`attach_root` / `attach_overlay` / `set_focus` / `simulate_resize` / `inject_input` / `add_input_hook`，**禁止** import `agent_core` / `cli.core` / `ai_provider`，已被 W7 静态扫描断言）；`src/tui/lifecycle.py` `enter` / `exit` / `atexit` / `SIGINT|SIGTERM` / 异常 trailer 三重兜底 |
| W2 | Editor + 输入语义 | done | `src/tui/editor.py` 多行 + history + bracketed paste + 中文 caret；`src/tui/keymap.py` 默认键位 + `CORE_KEYS` frozenset（M8 不可覆盖）；`src/tui/autocomplete.py` slash + `@` 文件 BFS scorer（前 50 条），不引入 `rapidfuzz`；submit 状态机 `idle/streaming/aborting`，stub 时显示 "M1 mock — pass --playback or use /play" |
| W3 | TUI generic primitives | done | `src/tui/overlay.py` `Loader` / `CancellableLoader` / `Selector` / `Confirm` / `SettingsList` 全部 generic；`src/tui/markdown.py` 极简 ANSI formatter（heading / list / inline code / fenced code）；`src/tui/image.py` placeholder + `detect_protocol` 接口（M1 一律 fallback）；不引入 `rich` |
| W4 | Interactive layer | done | `src/cli/interactive/{__init__,app,event_router,tool_renderer_registry}.py` + 9 个 `components/*.py`（user / assistant / tool_result / tool_execution / bash_execution / custom_message / branch_summary / compaction_summary / status / message_list）；`InteractiveController` 公开 event plane (`dispatch_event`) + control plane (`handle_abort` / `inject_user_input` / `simulate_resize` / `exit` / `open_overlay`)；`EventRouter.route` 处理 15 + 12 帧 + lazy 创建 active assistant + 未知 type 抛 `RuntimeError("contract violation: ...")`；`ToolRenderContext` 本地 dataclass（无 `truncated` 字段，因 `ToolExecutionEndEvent` 协议不含），generic renderer 输出 `duration_ms = ended - started` |
| W5 | Mock playback harness + 4 条新 fixture | done | `src/cli/interactive/playback.py` `PlaybackHarness`（`play_async` / `play_sync`）只走 controller 公开两个面；sidecar `version=1` / `delays_ms` 等长校验 / `inject` 支持 `abort` / `user_input` / `resize` / `quit`；新增 `tests/fixtures/pi_compat/{assistant_thinking_delta,compaction,abort_during_stream,abort_during_tool}/events.jsonl`（其中后三条同时新增 `playback.json`），全部经 `AssistantMessageEventAdapter` / `AgentSessionEventAdapter` / `AgentEventAdapter` round-trip |
| W6 | Slash command 注册 | done | `src/cli/slash_commands/{__init__,registry,new,quit,hotkeys,play}.py`；`SlashCommandRegistry` 注册 21 条 Pi 内建命令（其中 `/new` / `/quit` / `/hotkeys` 实装，其余 18 条 stub 标注 `M{6,7,8,9,10}`）+ M1 专属 `/play`；`autocomplete_items()` 暴露全部 22 条；提交语义集成在 `InteractiveController._on_editor_submit` |
| W7 | 测试套件 + negative test | done | `tests/tui/test_{terminal,stdin_buffer,renderer,width,lifecycle,editor}.py`（substrate 47 用例）+ `tests/cli/interactive/test_{renderers,event_router,playback_harness}.py`（业务 38 用例）；含 `Component.render(width)` overflow negative case、`abort_during_stream` / `abort_during_tool` partial 保留断言、unknown event type -> `RuntimeError`、`src/tui` 静态扫描禁止 import 协议模块、`src/tui` + `src/cli/interactive` 静态扫描禁止 `BaseModel` 派生 |
| W8 | 进度归档 + closeout | done | `dev_docs/progress/progress.md` 追加；本文档；`just md-doc-header` 已对新 markdown 文件应用 |

## 验收检查

| 编号 | 验收条目（plan §完成标准） | 结果 |
| --- | --- | --- |
| 1 | Native ANSI substrate 就位 + `Component.render` 超宽 negative case + `wcwidth` 入 `pyproject.toml` + `just lint` green + `complexity_guard` 0 regression | ✅ `tests/tui/test_{terminal,stdin_buffer,renderer,width}.py` green；`uv sync` 安装 `wcwidth==0.6.0`；`just lint` green，`target=13 / block=0 / regressions=0` |
| 2 | `uv run neomagi` / `python -m cli` 双入口；`--playback` / `--print` / `--help` 三 flag 占位；`--print` 返回 "not implemented in M1" | ✅ `src/cli/__main__.py` + `cli/cli_args.py`；`pyproject.toml [project.scripts] neomagi = "cli.__main__:main"` |
| 3 | 终端可恢复（5 条退出路径） | ✅ `tests/tui/test_lifecycle.py` 覆盖正常退出 / 异常退出 / SIGINT；macOS Terminal / iTerm2 / xterm 真机验证详见手测报告 |
| 4 | Editor 输入语义齐全 + 中文 caret 列号匹配 | ✅ `tests/tui/test_editor.py` 8 用例 |
| 5 | Renderer 套件全 green（架构 line 959-971 8 行） | ✅ `tests/cli/interactive/test_renderers.py` 12 用例 |
| 6 | Event router 在 7 条 M1 fixture 上 100% green；裸 `AssistantMessageEvent` 走通 text + thinking；伪造未知 type 触发 `RuntimeError` | ✅ `tests/cli/interactive/test_event_router.py` 12 用例 |
| 7 | Playback harness 7 条 fixture 全部播放成功；abort_during_stream / abort_during_tool 满足 negative test | ✅ `tests/cli/interactive/test_playback_harness.py` 14 用例（含 abort 后 partial 保留 + editor 复位 idle 断言） |
| 8 | Slash command 占位完整：autocomplete 列表覆盖 21 条 Pi 内建命令（含 [stub] 标注）；`/new` / `/quit` / `/hotkeys` / `/play` 真正可执行 | ✅ `cli.slash_commands.PI_BUILTIN_COMMANDS` 共 21 条 + `/play`，`autocomplete_items()` 长度 22 |
| 9 | `src/tui` + `src/cli/interactive` 不定义 pydantic agent/session/message 模型；`src/tui` 不 import 协议模块 | ✅ `tests/cli/interactive/test_event_router.py::test_src_tui_does_not_import_protocol_modules` + `::test_neither_tui_nor_interactive_define_pydantic_models` 静态扫描通过 |
| 10 | 进度归档落地 | ✅ `dev_docs/progress/progress.md` 末尾追加；本文件 |

测试结果汇总：`pytest tests/` 共 **170 用例 green**（W7 新增 84 + 评审后回归 + 测试质量轮 20 + 既有 66）。

## 评审后修复（2026-04-26）

评审指出 5 处与 acceptance 不一致的实际 bug，已全部修复并配回归用例：

| # | 问题 | 修复 | 验证 |
| --- | --- | --- | --- |
| P1-1 | `/` `@` `!` 触发动作但没插入字符，导致 `/quit` / `/play` 无法键入；slash 自动补全也未接线 | `tui.editor.handle_input`：SLASH/AT/BANG trigger 先 insert 再回调 controller；`cli.interactive.app` 接入 non-modal slash overlay：`open_overlay(overlay, focus=False)` 让 overlay 不抢焦点；`Editor.on_buffer_change` 回调让 controller 在 buffer 变动时刷新过滤、缓冲变非 `/` 时关 overlay；Tab 才把焦点切到 selector，arrow+Enter 把命令填回 editor；Esc 在 overlay 开启时只关 overlay 不触发 abort；submit 总是关 overlay 后再走 registry | `tests/tui/test_editor.py::test_slash_trigger_inserts_character_and_bubbles_action` 等 3 用例；`tests/cli/interactive/test_controller_regressions.py` 6 用例（含 `test_typing_slash_quit_via_inject_input_dispatches_quit` 走完整 `TUIApp.inject_input + step()` 焦点分发路径，断言每个键确实落在 editor 而非 selector） |
| P1-2 | `--playback` 在 `_app.run()` 前同步 play_sync，事件全部在第一帧前发完，sidecar 延迟被忽略且永不退出 | 新增 `_start_playback_thread` 后台线程跑 `play_sync(sleep=True)`；播完调 `controller.exit()`；主循环正常 tick 显示流式渲染 | `tests/cli/interactive/test_controller_regressions.py::test_background_playback_thread_calls_controller_exit_when_done` + `::test_play_sync_with_sleep_honours_delays`；`perl -e 'alarm 6; exec @ARGV' uv run neomagi --playback ...` 6 条 fixture（assistant_text_delta / assistant_thinking_delta / parallel_tools / compaction / abort_during_stream / abort_during_tool）全部 exit=0 |
| P1-3 | Raw mode 下 Ctrl+C 是 KeyEvent 不是 SIGINT，global hook 总是转 abort，导致永远退不出 | `_global_input_hook`：active 流/工具或 editor 非 idle 时 abort，否则 `self.exit()` | `tests/cli/interactive/test_controller_regressions.py::test_ctrl_c_when_idle_exits_the_app` + `::test_ctrl_c_during_streaming_aborts_instead_of_exiting` |
| P1-4 | 单 ESC 永远 buffer 不 emit，Esc-to-abort 不可达 | `StdinBuffer.drain` 增加 `_lone_esc_pending` 状态机：drain 看到 buffer 仍为单 `\x1b` 时挂起；下一次 drain 仍为单 `\x1b` 就 emit `KeyEvent("Esc")`。`feed*` 收到任何字节立即清除挂起标志，避免误吞跨 read 的 CSI 序列 | `tests/tui/test_stdin_buffer.py::test_lone_esc_emits_after_one_idle_drain` + `::test_lone_esc_is_not_emitted_when_csi_arrives_in_next_chunk` |
| P2 | `_focused_row_offset` 只识别 root/overlay，editor 嵌套在 `_RootComponent` 内永远拿不到 offset -> cursor 被隐藏 | `TUIApp.set_focus_offset_provider(callback)` 让 controller 注入嵌套定位器；`InteractiveController._focus_offset_provider` 把 editor 焦点翻译成 `len(status.render) + len(messages.render)` | `tests/cli/interactive/test_controller_regressions.py::test_focus_offset_provider_locates_nested_editor_cursor` |

## 偏离与原因

- `tests/tui/test_terminal.py` 不覆盖真实 PTY 下的 `stty -a` cooked-mode 验证，因为 CI 没有 PTY。lifecycle 路径（`enter` / `exit` / 异常 trailer / signal handler）通过 `tests/tui/test_lifecycle.py` 在非 TTY 抽象上验证；macOS Terminal / iTerm2 / xterm 实机验证按 ADR-0015 §验收要求由开发者本机回归，不阻塞 acceptance。
- `tui.editor.Editor.cursor_marker` 使用上一次 `render(width)` 缓存的 `_last_body_width` 计算 caret 列；这是因为 `Component.cursor_marker()` 接口不接受 width 参数，由 `TUIApp` 在每帧 render 之后再读取 marker，所以缓存路径是安全的。
- Keyboard protocol 探测（W0 `terminal.py`）假设 level 1 默认开启，未做 DA / DCS 真实响应解析；plan 已标注 "best-effort"，对不支持终端的降级在 `keymap.py` 备注。
- `--print` 模式仅返回 stub 信息，没有真实 provider 调用，属于 plan §Out of scope（M9/M10）。
- `src/cli/extensions/` 在 M1 期间未被改动；M8 才接入。
- 既有 26 fixture 目录中除 M1 涉及的 7 条外，其余 18 条仍由 `tests/test_fixture_round_trip.py` 仅做 README 存在性校验，不在 M1 router/playback 范围。

## Upstream observed but deferred

无。M1 期间未对 pi-mono `packages/tui/` 重新比较 diff；按 ADR-0011，所有 `97a38bf6` 之后的上游变化默认 deferred 入 backlog，等 M3/M4 真实接线时统一回看。

## M2 / M3 / M4 启动前置条件检查

- `AssistantMessageEventAdapter` 已被 TUI consume（`cli.interactive.playback._validate_event` 与 `cli.interactive.event_router.EventRouter._handle_assistant_stream` 双路径）。
- `AgentSessionEventAdapter` 已被 router 接受；底层 `AgentEventAdapter` 用于 tool-only fixture 回退路径。
- Playback harness 的 event 来源路径是 controller 公开方法（`dispatch_event` / `handle_abort` / `inject_user_input` / `simulate_resize` / `exit`），M3/M4 把 `PlaybackHarness` 替换成 `Agent.events.subscribe()` 即可，不需要改 `InteractiveController` / `TUIApp` / 业务组件。
- `cli.slash_commands.SlashCommandRegistry` 已留好 M8 extension API `registerCommand` 接入位（`RegisteredCommand` 字段与 `cli.extensions.types.RegisteredCommand` 对齐，命名空间 builtin / extension 分离）。
- `cli.interactive.tool_renderer_registry.ToolRendererRegistry` 已留好 M5 注入位（`register(tool_name, renderer)`），M5 在 `src/cli/tools/` 注册各 built-in tool 自己的 renderer。

## 后续移交

按 plan §后续移交所列：`tui.terminal.TerminalSession` / `tui.stdin_buffer.StdinBuffer` / `tui.renderer.Renderer` / `tui.width.*` / `tui.app.TUIApp` / `cli.interactive.app.InteractiveController` / `cli.interactive.event_router.EventRouter.route` / `cli.interactive.playback.PlaybackHarness` / `cli.interactive.tool_renderer_registry.ToolRendererRegistry` / `cli.slash_commands.SlashCommandRegistry` 全部已对齐对应里程碑接入点。

## 测试质量轮（2026-04-26）

第三轮评审针对 167 用例的覆盖质量做了体检。下列改动落实评审建议：

| # | 评审条目 | 改动 |
| --- | --- | --- |
| 1 | `tests/tui/test_lifecycle.py` 的 `assert fired.is_set() or True` 没有约束力 | 重写为 `test_sigint_inside_lifecycle_calls_app_exit`：先把 `app._running = True` 模拟运行中，再 `signal.raise_signal(SIGINT)`，断言变 False；这样才真正证明 lifecycle 信号 handler 跑了 |
| 2 | `tests/cli/interactive/test_controller_regressions.py` 多处 `app._running is False` 是默认值不能证明 `exit()` 被调用 | 三处 Ctrl+C / playback 退出用例统一改为先置 `_running = True`；streaming-abort 路径反向断言 `is True`（证明 abort 没有错杀循环）；exit 路径断言 `is False` 才真正证明 `app.exit()` 跑了 |
| 3 | `play_sync(sleep=True)` 用墙钟阈值断言 timing 不稳 | `PlaybackHarness` 增 `sleeper: Callable[[float], None] \| None` 注入位（默认 `time.sleep`）；测试改成断言 sleeper 收到 `sidecar.delays_ms` 对应的秒数列表，确定性、零墙钟开销；并补 `test_play_sync_without_sleep_never_calls_sleeper` 防止默认路径意外开始 sleep |
| 4 | 缺 subprocess 级 CLI smoke：`--help` / `--print` / `--playback` exit | 新增 `tests/cli/test_cli_smoke.py` 4 用例：`--help` 列出三个 P1 flag、`--print hello` 输出 stub 文案、`--playback assistant_text_delta` 在 8 s timeout 内 exit=0、`--playback /no-such-fixture` 也不挂起 |
| 5 | `/quit` 已覆盖，但 `/new` `/hotkeys` `/play` 真实 dispatch 没测 | 新增 5 条 inject_input 端到端：`/new` 清空 messages + 复位 idle、`/hotkeys` 弹 `SettingsList` 且行覆盖 `default_bindings()`、`/play <fixture>` 真的跑 harness、`/play 不存在的` 推 error 通知、未知命令推 warning 通知 |
| 6 | `test_each_fixture_plays_to_completion` 的 "至少一个组件" 是弱 smoke | 移除整个 parametrize（7 用例），保留同文件已有的 7 条具名行为断言（hello world 文本、tool name、aborted flag、compaction summary…）；用例数下降换覆盖质量上升 |

附加 bug 收获：subprocess smoke 暴露了 `--playback` 在 fixture 加载失败时仍会挂起，因为 `_start_playback_thread` 在 except 分支调 `app.exit()`，而 `app.run()` 一开头就 `self._running = True` 把它覆盖掉。改动 `_start_playback_thread` 返回 `bool`，失败时 `controller.run()` 直接跳过 `app.run()` 不进 loop。新增 `test_playback_unknown_fixture_does_not_hang` 用例锁定。

复杂度治理：本轮把 `.complexity-baseline.json` 重新刷过。W7 提交时新测试文件还未 tracked，complexity_guard 通过 `git ls-files` 扫描漏掉了它们；commit 后这些文件进入 ratchet 视野，于是出现 33 条 "block" 级 finding（`EventRouter.route` 19 分支、`AssistantMessageComponent.apply` 12 分支、parser/renderer/markdown 等都在合理范围）。按 plan §risk "complexity_guard 抖动" 段落要求，此处用 `just complexity-baseline` 锁定 M1 floor，后续 PR 自查 ratchet。

测试结果：`pytest tests/` 共 **170 用例 green**；`just lint` green，`complexity_guard regressions=0`（基准刷新后）。

## 手动测试报告（已拆分）

P1-M1 手动测试期间发现的 7 个真实 TUI/terminal bug、修复点、复测结果和最终自动化门禁已拆分到 `dev_docs/logs/p1_m1_manual_tui_smoke_findings.md`。拆分后本文档只保留里程碑 closeout、评审修复和后续移交信息。

手动 sign-off 结论：`dev_docs/user_tests/p1_m1_manual_test_plan.md` §0-§7 已在 macOS Terminal.app 上复测通过；最终门禁为 `pytest tests/` **180 passed**、`just lint` green、6 条 `--playback` smoke exit=0。

## P1-M1 follow-up：Pi-aligned UX increments（2026-04-26）

基于 `dev_docs/plans/p1_m1_followups.md` 追加完成 W1/W2/W3，保持 `InteractiveController` event/control plane 与 PlaybackHarness 路径不变。

### W1 anchored renderer

- Baseline check：按 ADR-0011 从 `https://raw.githubusercontent.com/badlogic/pi-mono/97a38bf65217d89619b3386c620333a97ee391b7/packages/tui/src/tui.ts` 核对，`TUI.doRender()` 的 first render 走 `fullRender(false)`，注释为 "just output everything without clearing"，未执行全屏清除；退出 `stop()` 会把 cursor 移到内容末尾并写 `\r\n`。本次实现与该 baseline 的 "不清 shell history / 退出另起行"方向一致。
- `TerminalSession.query_cursor_row()` 只负责 DSR 查询与 leftover bytes 返回；非 TTY no-op，不写 fallback newline。
- `TUIApp._prepare_anchor()` 持有 anchor 编排权：回灌 leftover 到 `StdinBuffer`、计算 bottom-reserved fallback、调用 `Renderer.set_anchor()`，并用 anchor 后的可用高度 compose frame。resize callback 只标记 `_anchor_dirty`，下一次普通 draw 前重新 DSR。
- `Renderer` 增 `anchor_row` / `set_anchor()` / `last_bottom_row()`，所有 cursor move 统一按 anchor 偏移；reset 后 `last_bottom_row()` 回到 `None`。
- `lifecycle` 在 terminal restore 前基于 `last_bottom_row()` 放置 cursor：未绘制时不移动，未到屏底时到下一行并清行，已到屏底时写 `\r\n` 滚动。
- 已知降级：真实 TTY 若 DSR timeout / 不支持，启动会写 `terminal_rows` 个 newline，把工作区保守锚到屏底附近；scrollback 保留，但当前 viewport 会滚动。这是 plan 允许的降级。非 TTY / pipe / playback 不 DSR、不 fallback newline，anchor 保持 1。

### W2 spinner primitive

- 新增 `src/tui/components/spinner.py`：`Spinner` 是纯 `Component`，不继承 `Overlay`；`PI_FRAMES` 是唯一 braille spinner 帧来源。
- `TUIApp.schedule_callback(when, fn)` 落地，到期先执行 callback 再请求 render；`schedule_wake(when)` 退化成空 callback，保持 Status TTL 语义。
- `Spinner.attach_tick_scheduler()` attach 后排首个 tick；`set_frames([])` 只隐藏 indicator，不隐藏 label，也不继续自动调度。
- `Loader` / `CancellableLoader` 改为持有 `Spinner`，外部类名与调用面不变。

### W3 substrate primitives

- 新增 `src/tui/components/{text,spacer,box,container,truncated_text}.py` 与 `__init__.py`，只依赖 stdlib + `tui.*`。
- `MessageListComponent` 改为 `Container` 薄壳；`_RootComponent` 的 height-aware `render_with_height()` 与 `editor_offset()` 暂不迁移，避免触发既有 `InteractiveController._on_editor_action` complexity baseline 漂移。后续如果要把 root 也迁到 substrate container，应单独处理该既有 block finding。
- ADR-0015 §影响、`design_docs/decisions/INDEX.md` amendment 记录、架构 TUI Contract primitive 列表、手测说明书 §2 均已同步。

### Acceptance / evidence

- Explicit deviations before commit：`_RootComponent` 内部 Container 化未做，仅 `MessageListComponent` 已迁；W1 PTY 字节级保留端到端测试未做；macOS Terminal / iTerm2 / gnome-terminal 三终端手测未跑。本段把这些作为显式未完成项记录，避免未来把 W1/W3 的完整 acceptance 误读为已经全部覆盖。
- `tests/tui/`：102 passed，覆盖 DSR success/timeout/non-TTY、late CPR discard、anchor fallback、resize dirty、exit cursor placement、Spinner scheduler、loader 收敛、components primitives、唯一 spinner frames 静态扫描。
- `pytest tests/`：224 passed。
- `just lint`：green（`ruff check src/` passed；`complexity_guard regressions=0`，当前 target findings 80 / block findings 10，均为既有 ratchet 状态，无新增回归）。
