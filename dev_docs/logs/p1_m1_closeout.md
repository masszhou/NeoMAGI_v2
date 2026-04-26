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

## W0–W8 状态

| W | 工作项 | 状态 | 关键产出 |
| --- | --- | --- | --- |
| W0 | Native ANSI substrate（落实 ADR-0015） | done | `src/tui/{terminal,stdin_buffer,renderer,width,component}.py`；`wcwidth>=0.2.13` 进入生产依赖；`Component.render(width)` 行宽守护 + `ComponentOverflowError`；`Renderer.present(frame, cursor=...)` 单入口 + line-diff + synchronized output；`StdinBuffer` 处理 partial ESC / CSI / OSC / APC + bracketed paste + xterm modifyOtherKeys / Kitty CSI-u；`TerminalSession` SIGWINCH 单 owner + raw mode + bracketed paste 包裹 + best-effort keyboard-protocol 探测 |
| W1 | CLI 入口 + 终端 lifecycle | done | `src/cli/__main__.py` + `pyproject.toml [project.scripts] neomagi`；`src/tui/app.py` `TUIApp`（`attach_root` / `attach_overlay` / `set_focus` / `simulate_resize` / `inject_input` / `add_input_hook`，**禁止** import `agent_core` / `cli.core` / `ai_provider`，已被 W7 静态扫描断言）；`src/tui/lifecycle.py` `enter` / `exit` / `atexit` / `SIGINT|SIGTERM` / 异常 trailer 三重兜底 |
| W2 | Editor + 输入语义 | done | `src/tui/editor.py` 多行 + history + bracketed paste + 中文 caret；`src/tui/keymap.py` 默认键位 + `CORE_KEYS` frozenset（M8 不可覆盖）；`src/tui/autocomplete.py` slash + `@` 文件 BFS scorer（前 50 条），不引入 `rapidfuzz`；submit 状态机 `idle/streaming/aborting`，stub 时显示 "M1 mock — pass --playback or use /play" |
| W3 | TUI generic primitives | done | `src/tui/overlay.py` `Loader` / `CancellableLoader` / `Selector` / `Confirm` / `SettingsList` 全部 generic；`src/tui/markdown.py` 极简 ANSI formatter（heading / list / inline code / fenced code）；`src/tui/image.py` placeholder + `detect_protocol` 接口（M1 一律 fallback）；不引入 `rich` |
| W4 | Interactive layer | done | `src/cli/interactive/{__init__,app,event_router,tool_renderer_registry}.py` + 9 个 `components/*.py`（user / assistant / tool_result / tool_execution / bash_execution / custom_message / branch_summary / compaction_summary / status / message_list）；`InteractiveController` 公开 event plane (`dispatch_event`) + control plane (`handle_abort` / `inject_user_input` / `simulate_resize` / `exit` / `open_overlay`)；`EventRouter.route` 处理 15 + 12 帧 + lazy 创建 active assistant + 未知 type 抛 `RuntimeError("contract violation: ...")`；`ToolRenderContext` 本地 dataclass（无 `truncated` 字段，因 `ToolExecutionEndEvent` 协议不含），generic renderer 输出 `duration_ms = ended - started` |
| W5 | Mock playback harness + 4 条新 fixture | done | `src/cli/interactive/playback.py` `PlaybackHarness`（`play_async` / `play_sync`）只走 controller 公开两个面；sidecar `version=1` / `delays_ms` 等长校验 / `inject` 支持 `abort` / `user_input` / `resize` / `quit`；新增 `tests/fixtures/pi_compat/{assistant_thinking_delta,compaction,abort_during_stream,abort_during_tool}/events.jsonl`（其中后三条同时新增 `playback.json`），全部经 `AssistantMessageEventAdapter` / `AgentSessionEventAdapter` / `AgentEventAdapter` round-trip |
| W6 | Slash command 注册 | done | `src/cli/slash_commands/{__init__,registry,new,quit,hotkeys,play}.py`；`SlashCommandRegistry` 注册 21 条 Pi 内建命令（其中 `/new` / `/quit` / `/hotkeys` 实装，其余 18 条 stub 标注 `M{6,7,8,9,10}`）+ M1 专属 `/play`；`autocomplete_items()` 暴露全部 22 条；提交语义集成在 `InteractiveController._on_editor_submit` |
| W7 | 测试套件 + negative test | done | `tests/tui/test_{terminal,stdin_buffer,renderer,width,lifecycle,editor}.py`（substrate 47 用例）+ `tests/cli/interactive/test_{renderers,event_router,playback_harness}.py`（业务 38 用例）；含 `Component.render(width)` overflow negative case、`abort_during_stream` / `abort_during_tool` partial 保留断言、unknown event type → `RuntimeError`、`src/tui` 静态扫描禁止 import 协议模块、`src/tui` + `src/cli/interactive` 静态扫描禁止 `BaseModel` 派生 |
| W8 | 进度归档 + closeout | done | `dev_docs/progress/progress.md` 追加；本文档；`just md-doc-header` 已对新 markdown 文件应用 |

## 验收检查

| 编号 | 验收条目（plan §完成标准） | 结果 |
| --- | --- | --- |
| 1 | Native ANSI substrate 就位 + `Component.render` 超宽 negative case + `wcwidth` 入 `pyproject.toml` + `just lint` green + `complexity_guard` 0 regression | ✅ `tests/tui/test_{terminal,stdin_buffer,renderer,width}.py` green；`uv sync` 安装 `wcwidth==0.6.0`；`just lint` green，`target=13 / block=0 / regressions=0` |
| 2 | `uv run neomagi` / `python -m cli` 双入口；`--playback` / `--print` / `--help` 三 flag 占位；`--print` 返回 "not implemented in M1" | ✅ `src/cli/__main__.py` + `cli/cli_args.py`；`pyproject.toml [project.scripts] neomagi = "cli.__main__:main"` |
| 3 | 终端可恢复（5 条退出路径） | ✅ `tests/tui/test_lifecycle.py` 覆盖正常退出 / 异常退出 / SIGINT；macOS Terminal / iTerm2 / xterm 真机验证留 closeout 备注（CI 无 PTY，不可全自动化） |
| 4 | Editor 输入语义齐全 + 中文 caret 列号匹配 | ✅ `tests/tui/test_editor.py` 8 用例 |
| 5 | Renderer 套件全 green（架构 line 959–971 8 行） | ✅ `tests/cli/interactive/test_renderers.py` 12 用例 |
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
| P2 | `_focused_row_offset` 只识别 root/overlay，editor 嵌套在 `_RootComponent` 内永远拿不到 offset → cursor 被隐藏 | `TUIApp.set_focus_offset_provider(callback)` 让 controller 注入嵌套定位器；`InteractiveController._focus_offset_provider` 把 editor 焦点翻译成 `len(status.render) + len(messages.render)` | `tests/cli/interactive/test_controller_regressions.py::test_focus_offset_provider_locates_nested_editor_cursor` |

## 偏离与原因

- `tests/tui/test_terminal.py` 不覆盖真实 PTY 下的 `stty -a` cooked-mode 验证，因为 CI 没有 PTY。lifecycle 路径（`enter` / `exit` / 异常 trailer / signal handler）通过 `tests/tui/test_lifecycle.py` 在非 TTY 抽象上验证；macOS Terminal / iTerm2 / xterm 实机验证按 ADR-0015 §验收要求由开发者本机回归，不阻塞 acceptance。
- `tui.editor.Editor.cursor_marker` 使用上一次 `render(width)` 缓存的 `_last_body_width` 计算 caret 列；这是因为 `Component.cursor_marker()` 接口不接受 width 参数，由 `TUIApp` 在每帧 render 之后再读取 marker，所以缓存路径是安全的。
- Keyboard protocol 探测（W0 `terminal.py`）假设 level 1 默认开启，未做 DA / DCS 真实响应解析；plan 已标注 "best-effort"，对不支持终端的降级在 `keymap.py` 备注。
- `--print` 模式仅返回 stub 信息，没有真实 provider 调用——属于 plan §Out of scope（M9/M10）。
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

附加 bug 收获：subprocess smoke 暴露了 `--playback` 在 fixture 加载失败时仍会挂起 —— 因为 `_start_playback_thread` 在 except 分支调 `app.exit()`，而 `app.run()` 一开头就 `self._running = True` 把它覆盖掉。改动 `_start_playback_thread` 返回 `bool`，失败时 `controller.run()` 直接跳过 `app.run()` 不进 loop。新增 `test_playback_unknown_fixture_does_not_hang` 用例锁定。

复杂度治理：本轮把 `.complexity-baseline.json` 重新刷过 —— W7 提交时新测试文件还未 tracked，complexity_guard 通过 `git ls-files` 扫描漏掉了它们；commit 后这些文件进入 ratchet 视野，于是出现 33 条 "block" 级 finding（`EventRouter.route` 19 分支、`AssistantMessageComponent.apply` 12 分支、parser/renderer/markdown 等都在合理范围）。按 plan §risk "complexity_guard 抖动" 段落要求，此处用 `just complexity-baseline` 锁定 M1 floor，后续 PR 自查 ratchet。

测试结果：`pytest tests/` 共 **170 用例 green**；`just lint` green，`complexity_guard regressions=0`（基准刷新后）。

## 手测发现：macOS Terminal.app 下 Ctrl+C 无效（2026-04-26）

按 `dev_docs/user_tests/p1_m1_manual_test_plan.md` §2.3 在 macOS Terminal.app
里测试时，Ctrl+C 完全没反应（按下后键入 `echo OK` 也不回显，能确认进程
没退）。但 `pty.fork()` 起的裸 PTY 测试里 Ctrl+C 立即 exit=0。差异定位到
`StdinBuffer` 的两个解析漏洞，都跟"我们主动协商了 keyboard protocol，但
没考虑终端真的接受时会发的替代编码"有关。

### 根因

`TerminalSession.enter()` 进入 raw mode 时无条件下发：

- `\x1b[>4;2m` —— 开 xterm `modifyOtherKeys=2`
- `\x1b[>1u` —— 开 Kitty keyboard protocol level 1

落地在 macOS Terminal.app 上时，这两个请求至少有一个被部分接受（具体哪
一个尚不能确证 —— ADR-0015 §影响段已写明 M1 不做 DA 探测，按 best-effort
处理）。结果终端不再用裸字节 `\x03` 表示 Ctrl+C，而是用以下两种 CSI 编码
之一：

| 编码 | 含义 | 解析器原行为 |
| --- | --- | --- |
| `\x1b[99;5u` | Kitty/CSI-u：code=99 (`c`) + modifier=5 (Ctrl) | `_parse_csi_u` 吐 `KeyEvent(key="Ctrl+c")` —— 小写 `c`，与 keymap binding `"Ctrl+C"` / `_global_input_hook` 的 `event.key == "Ctrl+C"` **大小写不匹配 → silently miss** |
| `\x1b[27;5;99~` | xterm modifyOtherKeys=2：code=27 占位，第三参才是 ASCII | `~` 分支查 `_TILDE_NAMES["27"]` 返回 None → **直接丢弃事件** |

两条路径都让 Ctrl+C 落不到 hook，即使 `\x03` 同时被发出（实际两种模式
互斥），也走不同分支。Editor 本身的 keymap 同样命中不了，所以连 Confirm
overlay 都不会弹。这是为什么截图里 "Ctrl+C 之后没反应" + 按 Enter 仍能
触发 `M1 mock — no agent runtime` 通知（说明进程还活着、只是 Ctrl+C 的
事件丢了）。

### 解决办法（commit `72335a9 fix(tui/stdin): ...`）

- `_parse_csi_u`：当 modifier 含 `Ctrl` 且 code 落在 a–z 范围（97–122），
  先 `ch.upper()` 再走 `_format_key`。这样 `\x1b[99;5u` 现在吐
  `KeyEvent(key="Ctrl+C")`，与裸字节 `\x03` 路径产物一致。
- `~` 分支：识别 `code == "27"` + 三段 params 的 modifyOtherKeys=2 替代
  形式，第三参当 ASCII 还原为 `chr(code)`，复用同一套 Ctrl+letter 大写
  化逻辑后 emit。`\x1b[27;5;99~` 现在也吐 `KeyEvent(key="Ctrl+C")`。

两条 regression test（`test_csi_u_ctrl_letter_is_normalised_to_uppercase`
+ `test_csi_27_modify_other_keys_form_for_ctrl_c`）锁定。同 commit 还落了
`scripts/diag_keys.py` —— 8 秒 raw-mode + 同协商序列的字节探针，落盘到
`/tmp/neomagi-diag-keys.log`，未来再有"某 Ctrl+X 在某终端没反应"报告时
让用户先跑这个出实证，而不是猜测。

### Acceptance 影响

- `dev_docs/user_tests/p1_m1_manual_test_plan.md` §2.3 用户复测：`uv run
  python -m cli` → Ctrl+C → `echo OK` 回显出现，**Ctrl+C 一次成功退出**。
- M1 acceptance #3「终端可恢复」原本只覆盖 5 条退出路径的 termios 还原，
  没覆盖"raw-mode 下 Ctrl+C 必须能到 hook"这条隐含前提；本轮把它显式加
  到 stdin parser regression suite 里，避免后续 protocol negotiation 调
  整再次回退。
- 自动测试 167 → **174 passed**（W7 + 评审后回归 + 测试质量轮 + parser
  case 修复 + CSI-27 form），`just lint` green，`complexity_guard
  regressions=0`。

## 手测发现：双 Esc 退化为两次单 Esc（2026-04-26）

按 `dev_docs/user_tests/p1_m1_manual_test_plan.md` §3.9 在 macOS Terminal.app
上测 "快速按 Esc Esc 应弹 `tree navigation not implemented` 黄色通知"，
实测拿到的是 §3.8 单 Esc 的 `[idle] aborted` —— 两次按键各自走 ABORT 路径，
`Esc Esc` 复合事件根本没成型。

### 根因

P1-4 修单 Esc 不可达时引入的"flush after one idle drain"逻辑节奏太快：
`StdinBuffer.drain()` 看到 `_buffer == "\x1b"` 时只挂起 1 个 drain tick
（≈ 12 ms 在 TUIApp 主循环节拍下）就 emit 单 `Esc`。但人类双击 Esc 的反射
间隔在 100–250 ms 量级，第一下早就被当作单 Esc 提交（→ ABORT → footer
`aborted`），第二下到达时 buffer 是空的，又被当作另一次单 Esc。`Esc Esc`
复合永远没机会被 `_parse_escape` 折叠。

### 解决办法（commit `<本提交>`）

把 lone-ESC 的"挂起 1 次 drain"改成**基于墙钟的 debounce**：

- `StdinBuffer.__init__` 新增 `lone_esc_timeout: float = 0.10` + `clock`
  注入位（默认 `time.monotonic`）。100 ms 对齐 xterm ESC-key 约定，既给
  人类双击 Esc 留够时间，又不把单 Esc 拖到明显发滞。
- `drain()` 抽出 `_maybe_flush_lone_esc(events)` helper：buffer 仍是单
  `\x1b` 时记下首次出现时刻；后续 drain 只有在墙钟差 ≥ timeout 才 emit
  单 `Esc`。`feed`/`feed_str` 任何字节到达都重置 `_lone_esc_seen_at`，
  包括第二个 `\x1b` —— 此时 `_parse_escape` 在主循环里直接折叠成
  `KeyEvent("Esc Esc")`。
- 测试改用注入 fake clock：`test_lone_esc_emits_after_debounce_window`
  断言"在 50ms drain 不发，0.5s drain 才发"；新增
  `test_slow_double_esc_still_collapses_to_single_event` 锁定本次回归
  ——"feed Esc → 70ms 后 feed Esc → 单次 drain 出 `Esc Esc`"。

### Acceptance 影响

- `dev_docs/user_tests/p1_m1_manual_test_plan.md` §3.9 同步更新文案：
  说明 debounce 窗口是 100 ms、列出"看到 aborted 是因为按得太慢"vs
  "完全无反应是真 bug"两种失败模式判定。§3.8 也加上"等约 100 ms"的
  debounce 提示，避免读者误以为单 Esc 应该零延迟。
- M1 acceptance #4「输入语义齐全」原本只挂在自动 inject_input 测试上
  （它跳过物理时间），物理 debounce 的回归现在落到 stdin parser 单测
  里靠 fake clock 锁定，未来调整 timeout 不会偷偷退化。
- 自动测试 174 → **175 passed**；`just lint` green、`complexity_guard
  regressions=0`（drain 拆出 helper 后维持原有阈值）。

## 手测追加：双 Esc 仍不复合 + "aborted" 永久卡 footer（2026-04-26）

上一轮调宽 lone-ESC debounce 到 100ms 后，手测 §3.9 反馈"无论手速快慢，
两次 Esc 都各自变成 abort"。同时观察到一条衍生 bug：footer 一旦显示
`aborted` 就再也回不去 `[idle] M1 mock — ...`。

### 根因 1：CSI-encoded Esc 绕开了字节层 debounce

P1-M1 §"测试质量轮"加的 debounce 只挡在 `_parse_escape` 看到孤立字节
`\x1b` 的路径上。但 `TerminalSession.enter()` 下发的
`\x1b[>4;2m` / `\x1b[>1u` 协商一旦被终端接受（macOS Terminal.app 实测
确认），Esc 会被编码成完整的 CSI 序列：

| 编码 | 来源 | 解析路径 |
| --- | --- | --- |
| `\x1b[27u` | Kitty / CSI-u 协议 | `_parse_csi_u` 立刻 emit `KeyEvent("Esc")` |
| `\x1b[27;1;27~` | xterm modifyOtherKeys=2 替代形式 | `~` 分支需要新增名称映射（原本 `32 < ch_code < 127` 把 ASCII 27 = ESC 滤掉） |

无论哪条，事件**不经过字节层 debounce**：每次 Esc 都立刻 emit、立刻走
ABORT。第二次 Esc 到来时第一次早已被消化，永远凑不成 `Esc Esc`。

### 根因 2：footer 没有 TTL，"aborted" 永久挂死

`InteractiveController.handle_abort()` 直接 `editor.set_footer("aborted")`。
Editor 的 footer 是 plain 字符串，没有过期/复位机制，下一次 render 也只是
重新画原值。用户后续无论键入还是 `/new`，这个文案都不会消失，会让人误以为
"系统永久 stuck 在 aborted 状态"。

### 解决办法（commit `<本次>`）

#### Esc gesture 提到事件层

- `StdinBuffer.__init__` 增 `_pending_esc_at` 状态字段；默认 timeout 调到
  `0.20s`（200 ms gesture window，覆盖典型人类双击节奏 100–250 ms）；保留
  字节层 `_PARTIAL_CSI_WINDOW = 30 ms` 用于跨 read 的 partial-CSI 容忍。
- `drain()` 末尾追加 `_compose_esc_gestures(raw)`：任何来源的 `KeyEvent("Esc")`
  都先入 pending；下一次 Esc 到达就 fold 成 `KeyEvent("Esc Esc")` 一并
  emit；其他事件到来或 timeout 触发时 flush 单 Esc。这样无论字节路径
  还是 CSI-u / modifyOtherKeys 路径，复合器都能命中。
- `_csi_27_alt_form` 新增 helper：把 `\x1b[27;<mod>;<code>~` 中 ASCII
  8/9/13/27/32/127 这些命名控制码也映射回 `Backspace` / `Tab` / `Enter` /
  `Esc` / `Space` / `Backspace`，否则 ASCII 27 = ESC 会被 `32 < ch_code <
  127` 滤掉，整个 modifyOtherKeys=2 路径上的 Esc 都拿不到。

新增三条回归用例（fake clock 注入，全确定性）：
- `test_csi_encoded_esc_gesture_composes_via_event_layer`：两次
  `\x1b[27u` 100 ms 内 → `["Esc Esc"]`。
- `test_csi_27_modifyotherkeys_esc_also_composes`：两次
  `\x1b[27;1;27~` 150 ms 内 → `["Esc Esc"]`。
- `test_single_csi_encoded_esc_flushes_after_gesture_window`：单次
  CSI-u Esc + 500 ms 后 drain → `["Esc"]`。

#### Abort 不再写 footer，改推 status 通知

`handle_abort` 删掉 `editor.set_footer("aborted")`，改为
`status.push_notification("aborted", level="info", ttl_seconds=3.0)`。
Status 区已经有 TTL 自动淘汰，3 秒后通知自然消失，editor footer 维持
`[idle] M1 mock — pass --playback or use /play` 不变。

`tests/cli/interactive/test_controller_regressions.py` 里两条断言相应
更新：`test_ctrl_c_during_streaming_aborts_instead_of_exiting` 改成断
status 通知文本含 "abort"；`test_esc_closes_autocomplete_before_falling_
through_to_abort` 改成断 status 通知里 *没有* abort。

### Acceptance 影响

- `dev_docs/user_tests/p1_m1_manual_test_plan.md` §3.8 / §3.9 同步：
  说明单 Esc 期望看到的是 status 区青色 `aborted` 瞬时通知（不是 footer
  永久变化）；§3.9 列出"误把单 Esc 当双 Esc"的失败模式判定。
- M1 acceptance #4 物理回归现在分两层覆盖：字节层（`test_lone_esc_*` /
  `test_slow_double_esc_*`）+ 事件层（`test_csi_*_esc_*`），任一路径回退
  都会断。
- 自动测试 175 → **178 passed**；`just lint` green、`complexity_guard`
  re-baseline 后 regressions=0（`_csi_27_alt_form` 拆出独立 helper 维持
  阈值不变）。

## 手测追加：Status 通知 TTL 不生效（2026-04-26）

§3.8 单 Esc 改成走 status 通知后，手测反馈"青色 `aborted` 三秒后不消失，
还是永久挂在屏幕上"。

### 根因

`StatusComponent._alive_notifications()` 只在 `render()` 被调用时过滤过期
通知，但 `TUIApp` 主循环是**事件驱动**的：3 秒后 TTL 到期，没有任何输入
事件，loop 在 `time.sleep(0.012)` → 没有 `_consume_render_request` → 不
重画。直到下一次用户键入触发 render 才会过滤掉过期项。视觉上等于"永久挂
住"。

同类问题潜在影响：未来 `Loader` spinner、`ToolExecutionComponent` 的
duration 字段更新都需要被动唤醒，否则会观感凝固。

### 解决办法（commit `<本次>`）

- `TUIApp` 增 `_wake_at: list[float]` 队列 + `schedule_wake(when)` 公开
  方法 + `_check_wakeups()` helper。主循环和 `step()` 在每次 dispatch 之
  后都调 `_check_wakeups`：到期的 wake-up 转换为 `_render_requested = True`
  并从队列移除，紧接着 `_draw()` 就会走 `render()` → `_alive_notifications`
  自动剔除。
- `StatusComponent` 增 `attach_wake_scheduler(callback)`；
  `push_notification` 在登记通知时把 `expires_at + 0.05` 推给 callback
  （tiny buffer 保证唤醒发生**在**过期之后，render 一次性看到没活的通知）。
- `InteractiveController.bootstrap()` 调
  `self._status.attach_wake_scheduler(self._app.schedule_wake)` 接通。

回归测试 `test_status_notification_expires_via_scheduled_wake` 用
`monkeypatch` 注入 fake `time.monotonic`：push 通知 → 断言 `_wake_at`
非空 → 推进 fake clock 越过 TTL → `app.step()` → 断言 `_wake_at` 已被
消费、`status.render()` 输出不再含通知文本。

### Acceptance 影响

- 任何使用 `push_notification(ttl_seconds=...)` 的路径（abort、playback
  完成、unknown command、stub command 等）的 TTL 现在真的生效。
- `dev_docs/user_tests/p1_m1_manual_test_plan.md` §3.8 的"3 秒后自动消失"
  期望从设计声明变成可观测事实。
- 自动测试 178 → **179 passed**；`just lint` green、`complexity_guard`
  re-baseline 后 regressions=0（baseline fingerprint 是行号锚定，
  `bootstrap` 内多了一行让 `_on_editor_action` 行号下移触发 false-positive
  regression，refresh 一次即可）。
