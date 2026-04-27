---
doc_id: 019dd0cf-6a8a-74d2-9fa9-23eda6b52106
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-27T23:19:11+02:00
---
# P1-M1 Manual TUI Smoke Findings

- Status: done
- Date: 2026-04-26
- Scope: `dev_docs/user_tests/p1_m1_manual_test_plan.md`
- Plan: `dev_docs/plans/p1_m1_tui_skeleton_and_mock_playback.md`

## 总结

P1-M1 TUI / mock playback 手动测试已完成并通过。手测覆盖：

- 全新环境准备与基础 CLI smoke；
- TUI 启动、渲染、退出、终端恢复；
- Editor 输入、中文 caret、Esc / Ctrl+C / Tab 等键盘路径；
- slash command 自动补全、`/new` / `/quit` / `/hotkeys` / `/play`；
- mock playback fixture 的正常流、thinking、parallel tools、compaction、abort；
- status 通知、selector focus、消息列溢出、tool abort 视觉语义。

本轮手测发现并修复了 7 个真实问题，另补齐手动测试说明书中的操作路径、失败模式和诊断工具说明。

## 修复 1：macOS Terminal.app 下 Ctrl+C 无效

真实手测现象：按 `Ctrl+C` 完全没反应，进程未退出，后续输入仍由 TUI 接管。

根因：

- raw mode 下 `Ctrl+C` 不再作为 SIGINT 进入 Python signal handler，而是走 `StdinBuffer` 的 key event 路径；
- `TerminalSession.enter()` 下发 xterm `modifyOtherKeys=2` 与 Kitty CSI-u keyboard protocol 协商后，macOS Terminal.app 可能把 Ctrl+C 编成 `\x1b[99;5u` 或 `\x1b[27;5;99~`；
- CSI-u 路径吐出 `KeyEvent(key="Ctrl+c")`，大小写与 `"Ctrl+C"` binding 不匹配；
- xterm modifyOtherKeys `27;<mod>;<ascii>~` 形式未被 parser 识别，事件被丢弃。

修复：

- `_parse_csi_u` 对 Ctrl+letter 统一转大写；
- 新增 CSI-27 alternative form 解析，支持 modifyOtherKeys=2 中的 ASCII 命名码；
- 落地 `scripts/diag_keys.py` 作为后续按键问题的 raw-mode 字节探针。

验收结果：手测 §2.3 复测 `uv run python -m cli` 后按 `Ctrl+C` 一次成功退出，终端回显恢复。

## 修复 2：双 Esc 退化为两次单 Esc

真实手测现象：快速按 `Esc Esc` 没有触发 tree navigation placeholder，而是两次走单 Esc abort。

根因：

- P1-4 为修单 Esc 不可达而加入的 lone-ESC flush 只挂起一个 drain tick，约 12 ms；
- 人类双击 Esc 常见间隔在 100-250 ms，第一下会过早被当成单 Esc 提交；
- 第二下到达时 buffer 已空，无法组成 `Esc Esc`。

修复：

- `StdinBuffer` 增加基于 monotonic clock 的 lone-ESC debounce；
- 默认窗口先调到 100 ms，并用 fake clock 测试锁定；
- 后续见修复 3，把 Esc 复合进一步提升到事件层。

验收结果：单 Esc 仍可触发 abort；双 Esc 手势不再被 12 ms drain tick 提前拆开。

## 修复 3：CSI-encoded Esc 绕过 debounce，且 aborted 永久卡 footer

真实手测现象：调宽 lone-ESC debounce 后，物理终端里双 Esc 仍然不复合；同时 footer 一旦显示 `aborted` 就不会恢复。

根因：

- 终端接受 keyboard protocol 后，Esc 可能编码为 `\x1b[27u` 或 `\x1b[27;1;27~`；
- 这两条都是完整 CSI event，不经过字节层 lone-ESC debounce；
- `handle_abort()` 直接改 editor footer，footer 没有 TTL，视觉上永久卡住。

修复：

- 在事件层新增 Esc gesture composer：任何来源的 `KeyEvent("Esc")` 都进入 200 ms 复合窗口；
- 第二个 Esc 在窗口内到达时折叠为 `KeyEvent("Esc Esc")`；
- 单 Esc 超时后再 flush；
- `handle_abort()` 不再改 footer，改为推 status 通知 `aborted`。

新增回归覆盖：

- 两次 CSI-u Esc 在 100 ms 内合成 `Esc Esc`；
- 两次 modifyOtherKeys Esc 在 150 ms 内合成 `Esc Esc`；
- 单次 CSI-u Esc 超过 gesture window 后 flush 为单 Esc。

## 修复 4：Status 通知 TTL 不生效

真实手测现象：青色 `aborted` status 通知 3 秒后不消失，像永久挂在屏幕上。

根因：

- `StatusComponent._alive_notifications()` 只在 `render()` 时过滤过期通知；
- `TUIApp` 主循环是事件驱动，TTL 到期时如果没有输入事件，就不会触发重绘；
- 视觉上通知会一直留到下一次用户输入。

修复：

- `TUIApp` 增加 `schedule_wake(when)` 与 `_check_wakeups()`；
- `StatusComponent.attach_wake_scheduler()` 在 push notification 时登记过期后的 wake-up；
- controller bootstrap 接通 status wake scheduler。

验收结果：手测 §3.8 的 `aborted` 通知会按 TTL 自动消失。后续 Loader spinner、tool duration 等定时刷新也有统一入口。

## 修复 5：Tab 进 picker 后视觉无感

真实手测现象：Tab 进入 selector 后看起来像系统锁住，只有 Esc 有反应。

根因：

- Tab 实际已经把焦点从 editor 移到 selector，但 macOS Terminal 默认 cursor 很弱；
- selector focused / unfocused 渲染完全一致，选中行只有一个 `▶`；
- 用户看不到焦点变化，误以为按键失效。

修复：

- `Component` 增加 `focused: bool`；
- `TUIApp.set_focus()` 维护旧/新 focus 状态并请求重绘；
- `Selector.render_body()` 在 focused 时 title 加粗青色，追加 `[active ...]` 提示，并对选中行使用 inverse video。

验收结果：手测 §4.4 能明确看到 selector title 变青色加粗、选中行反色；Tab focus 迁移不再需要依赖弱 cursor。

## 修复 6：消息列溢出从底部裁剪导致 editor 不可见

真实手测现象：多次 `/play` 后像是每次都重新刷新，没有从历史继续往下走；editor 和最新消息会消失。

根因：

- `_compose_frame` 用 `lines[: self._rows]` 从底部裁掉超出内容；
- root 输出顺序是 status -> messages -> editor；
- 行数超过终端高度时，editor 和最新消息都被裁出可视区，只剩旧内容。

修复：

- `_RootComponent.render_with_height(width, height)` 改为高度感知三段布局；
- status pin 顶部，editor pin 底部，messages 使用剩余高度；
- messages 溢出时从顶部裁掉最老行，保留最新消息和 editor；
- `editor_offset(width)` 使用裁剪后的可见消息行数计算 cursor row。

验收结果：message-heavy 场景下 editor 始终可见，最新消息保留在屏幕底部；M1 不实现 in-app scroll，历史导航留给后续 session manager。

## 修复 7：abort_during_tool 视觉像“完成后再 abort”

真实手测现象：`/play abort_during_tool` 看到 tool 走完整 result，最后再追加 `[aborted]`，不像中途中断。

根因：

- fixture 原本包含 start -> update -> end，且 playback 在 update 后 inject abort 后仍继续投递 end；
- `ToolExecutionComponent.mark_aborted()` 会伪造 `_result = {"aborted": True}`，renderer 误走 completed/error result 分支；
- 视觉语义变成“完成了一个 error result，再额外被标 aborted”。

修复：

- `abort_during_tool/events.jsonl` 砍到 start + update，`playback.json` `delays_ms` 同步改为 `[0, 40]`；
- `mark_aborted()` 只设 aborted 状态和结束时间，不再伪造 result / is_error；
- `ToolRenderContext` 增加 `aborted` 字段；
- `generic_tool_renderer` 新增 aborted 分支，保留 partial 并显示 `[aborted after N ms]`。

验收结果：手测 §5.3 期望变为 partial + `[aborted after N ms]`，不应出现 `result [...]` 行。

## 文档准确性修订

本轮同步修订 `dev_docs/user_tests/p1_m1_manual_test_plan.md`：

- CLI 调用约定从 `uv run neomagi` 切到 `uv run python -m cli`；
- §2.5 增加 cooked mode / raw mode 术语速记；
- §3 增加 “Enter 在 mock 下会清 buffer + 推 mock 通知” 提醒；
- §3.1 / §3.8 / §3.9 明确单 Esc、双 Esc、Ctrl+C 的失败模式；
- §4.3 重写 selector 边输入边过滤语义；
- §4.4 增加 focus 视觉判定；
- §4.9 说明重复同一 fixture 视觉像刷新，以及消息溢出时 editor/status 始终可见；
- §5.3 重写 `abort_during_tool` 期望；
- §5.5 fixture 数补回 W5 deliverable 表的 7 条；
- §7 修正 `/quit` 双 Enter、`pgrep` pattern 与 Confirm 键序；
- 新增 `scripts/diag_keys.py` 作为后续“按键不反应”诊断工具。

## 手动测试最终判定

以下 P1-M1 手测项均通过：

- 环境准备与标准命令入口；
- `--help` / `--print` / `--playback` CLI smoke；
- TUI 启动、退出、终端恢复；
- Ctrl+C idle exit / active abort；
- 单 Esc abort、双 Esc placeholder；
- slash command typing、filtering、Tab picker、Enter submit；
- `/new` / `/quit` / `/hotkeys` / `/play`；
- 7 条 M1 playback fixture；
- status TTL、message overflow、tool abort 视觉语义。

以下不属于 P1-M1 手动测试 pass/fail：

- 真实 provider 对话；
- 真实 agent loop；
- 真实工具执行；
- session manager、历史滚动、extension runtime；
- Windows 终端。

## 自动化验证

最终通过：

```text
pytest tests/
just lint
6 条 --playback smoke
```

结果：

- full `tests/`：180 passed；
- `just lint`：ruff passed，`complexity_guard regressions=0`；
- playback smoke：`assistant_text_delta`、`assistant_thinking_delta`、`parallel_tools`、`compaction`、`abort_during_stream`、`abort_during_tool` 均 exit=0；
- 不存在 fixture 也不挂死。

## 结论

P1-M1 TUI skeleton + mock playback 已完成真实终端手动 sign-off。M1 范围内的 native ANSI runtime、input parser、interactive controller、slash command、playback harness、status notification、message layout 和 abort rendering 都已通过当前手动与自动化验收。M2/M3/M4 可继续沿用 controller 公开 event/control plane，把 playback source 替换为真实 agent event source。
