---
doc_id: 019dc595-6464-72b2-84b6-d519d9c85ecf
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T18:57:04+02:00
---

## 2026-04-25 20:49 (local) | P1-M0 closeout
- Status: done
- Done: 完成 P1-M0 Pi Baseline & Fixture Scaffolding：包骨架、协议类型、overflow / usage 常量、26 条 fixture 目录（8 条核心含 input+expected）、behavior matrix、TUI playback 协议、pi-mono 基线索引。
- Evidence: `dev_docs/logs/p1_m0_closeout.md`, `design_docs/architecture/pi_mono_baseline.md`, `design_docs/architecture/pi_behavior_matrix.md`, `design_docs/architecture/tui_playback_format.md`, `src/{ai_provider,agent_core,cli,tui,storage,policy,infra}/`, `tests/test_overflow.py`, `tests/test_fixture_round_trip.py`（共 66 用例 green）, `just lint` green, `complexity_guard` 0 regression.
- Next: 进入 P1-M1（TUI skeleton + mock playback harness）。
- Risk: 无；已记录 upstream observed but deferred 与 M1 前置条件，详见 closeout 文档。

## 2026-04-26 00:46 (local) | P1-M1 closeout
- Status: done
- Done: 完成 P1-M1 TUI Skeleton + Mock Playback：自研 native ANSI substrate（terminal/stdin_buffer/renderer/width/component）落实 ADR-0015；`TUIApp` + lifecycle + Editor + 22 条 slash command（21 Pi 内建 + `/play`）+ 9 个业务组件 + EventRouter + ToolRendererRegistry + PlaybackHarness 全部就位；W5 deliverable 表 7 条 fixture（含 4 条 M1 新增 events.jsonl + 3 条 playback.json sidecar）100% 播放成功，`abort_during_stream` / `abort_during_tool` negative test 满足 partial 保留 + editor 复位 idle 要求。
- Evidence: `dev_docs/logs/p1_m1_closeout.md`, `dev_docs/plans/p1_m1_tui_skeleton_and_mock_playback.md`, `src/tui/{terminal,stdin_buffer,renderer,width,component,app,lifecycle,editor,keymap,autocomplete,overlay,markdown,image}.py`, `src/cli/{__main__,cli_args}.py`, `src/cli/interactive/`, `src/cli/slash_commands/`, `tests/tui/test_*.py`, `tests/cli/interactive/test_*.py`, `tests/fixtures/pi_compat/{assistant_thinking_delta,compaction,abort_during_stream,abort_during_tool}/`（共 151 用例 green：W7 新增 85 + 既有 66）, `just lint` green, `complexity_guard` 0 regression（target=13/block=0）。
- Next: 进入 P1-M2（真实 / faux provider + AI stream contract）。
- Risk: 无；M2/M3/M4 接入路径已在 closeout 文档列出（PlaybackHarness 走 controller 公开 event/control plane 两个面，可直接替换为 `Agent.events.subscribe()`）。

## 2026-04-26 02:10 (local) | P1-M1 post-review fixes
- Status: done
- Done: 处理评审指出的 5 处 P1/P2 实际 bug：(1) `/` `@` `!` 现在既插入字符又触发 controller 接线的 slash 自动补全 overlay；(2) `--playback` 改为后台线程 `play_sync(sleep=True)` 驱动，sidecar 延迟生效，播完自动 `controller.exit()`；(3) raw mode 下 Ctrl+C 在 idle 时退出 lifecycle、有 active 流/工具时 abort；(4) `StdinBuffer` 增加 lone-ESC pending 状态机，单 Esc 经 1 个 drain tick 后 emit `KeyEvent("Esc")`，跨 read 的 CSI 序列不被误吞；(5) `TUIApp.set_focus_offset_provider` 让嵌套 root 内的 editor 焦点能正确翻译为绝对 cursor row。
- Evidence: `dev_docs/logs/p1_m1_closeout.md` § 评审后修复, `tests/cli/interactive/test_controller_regressions.py`（7 用例）+ `tests/tui/test_editor.py` / `test_stdin_buffer.py` 5 用例新增；`pytest tests/` 共 **163 用例 green**, `just lint` green, `complexity_guard` 0 regression；`uv run neomagi --playback tests/fixtures/pi_compat/{assistant_text_delta,abort_during_stream,parallel_tools,compaction}` 全部 exit=0。
- Next: 同上，进入 P1-M2。
- Risk: 无。

## 2026-04-26 02:55 (local) | P1-M1 second-round review fix
- Status: done
- Done: 二轮评审指出第一次 P1-1 修复让 slash overlay 抢焦点导致 `/quit` 跨字符无法键入。改为 non-modal 自动补全：`TUIApp.open_overlay(overlay, focus=False)` 不动焦点；`Editor.on_buffer_change` 回调让 controller 边输入边过滤；Tab 才把焦点送进 Selector，arrow+Enter 把所选命令填回 editor；Esc 在 overlay 开启时只关 overlay 不触发 abort；submit 总会先关 overlay 再走 registry。回归测试改用 `TUIApp.inject_input + step()` 走完整焦点分发路径。
- Evidence: `dev_docs/logs/p1_m1_closeout.md` § 评审后修复（更新 P1-1 行）, `tests/cli/interactive/test_controller_regressions.py` 现 11 用例（新增 4 条 inject_input 端到端：non-focused overlay、`/quit\n` 全程 editor 焦点、Tab→arrow→Enter、Esc 仅关 overlay）；`pytest tests/` 共 **167 用例 green**, `just lint` green, `complexity_guard` 0 regression；6 条 playback fixture smoke 全部 exit=0。
- Next: 进入 P1-M2。
- Risk: 无。

## 2026-04-26 03:30 (local) | P1-M1 test-quality round
- Status: done
- Done: 第三轮评审针对测试质量做了 6 处改进：(1) lifecycle SIGINT 用例去掉 `or True`，改为先置 `_running=True` 再断 `False`；(2) 三处 Ctrl+C / playback 退出用例同样补预置，streaming-abort 反向断言 `is True`；(3) `PlaybackHarness` 增 `sleeper` 注入位，timing 测试改为断 sleeper 收到 `delays_ms` 列表（不再依赖墙钟阈值）；(4) 新增 `tests/cli/test_cli_smoke.py` 4 条 subprocess 级 CLI smoke（`--help` / `--print` / `--playback` exit / 不存在 fixture 不挂起）；(5) `/new` `/hotkeys` `/play` 各补 inject_input 端到端 dispatch 测试；(6) 移除弱 smoke `test_each_fixture_plays_to_completion` parametrize（7 用例）以提高覆盖比例。subprocess smoke 顺带暴露并修复一处真实 bug：`--playback` 在 fixture 加载失败时挂起（`_start_playback_thread` 在 except 里调 `app.exit()`，但 `app.run()` 入口 `_running=True` 又覆盖回去；改 `_start_playback_thread` 返回 `bool`，失败时 `controller.run()` 直接跳过进入 loop）。
- Evidence: `dev_docs/logs/p1_m1_closeout.md` § 测试质量轮, `tests/cli/test_cli_smoke.py`, `tests/cli/interactive/test_controller_regressions.py`（17 用例，新增 5 dispatch + 2 sleeper + 修 4 假断言）, `tests/tui/test_lifecycle.py`, `src/cli/interactive/{playback,app}.py`；`pytest tests/` 共 **170 用例 green**；`.complexity-baseline.json` 刷新（W7 提交时新测试文件未 tracked，导致 complexity_guard 漏扫；本轮按 plan §risk 锁 M1 floor），`just lint` green、`complexity_guard regressions=0`。
- Next: 进入 P1-M2。
- Risk: 无。

## 2026-04-26 17:10 (local) | P1-M1 manual test sign-off
- Status: done
- Done: `dev_docs/user_tests/p1_m1_manual_test_plan.md` §0–§7 全部章节在 macOS Terminal.app 上**手动复测通过**。期间发现并修了 7 处真 bug + 一批文档准确性问题，全部已落 commit 与 closeout。bug 清单：(1) `72335a9` macOS Terminal Ctrl+C 不走 hook（CSI-u 大小写 + CSI-27 modifyOtherKeys=2 形式两条 parser 漏洞）；(2) `bcace2a` lone Esc 12ms debounce 太短，调到 100ms 字节层；(3) `2520cdc` 事件层 Esc 复合器 + abort 改瞬时通知（CSI-encoded Esc 绕开字节层；footer 永久卡 aborted）；(4) `f47f554` status notification TTL 不生效（render loop 不被唤醒，新增 `TUIApp.schedule_wake` + StatusComponent 接通）；(5) `8df7e46` Selector Tab focus 视觉无感（加 `Component.focused` 字段 + title 加粗 + 选中行反色）；(6) `65a8c29` 消息列溢出从底部裁掉 editor（改高度感知三段布局：status/messages/editor，messages 从顶部裁老的）；(7) `a610723` `abort_during_tool` fixture 含多余 end event + `mark_aborted` 伪造假 result 导致 renderer 误判（fixture 砍掉 end event；`ToolRenderContext` 增 `aborted` 字段；renderer 新增 aborted 分支保留 partial）。文档准确性：CLI 调用约定改 `python -m cli`、§2.5 加 cooked mode 术语速记、§3 加 "Enter 在 mock 下清 buffer" 提醒、§4.4 / §4.9 重写期望、§7 pgrep pattern 修正、Confirm 键序更正、§5.5 fixture 数从 6 补回 7。
- Evidence: `dev_docs/logs/p1_m1_closeout.md` 末尾 7 段"手测追加"完整记录每条根因 / 修法 / acceptance 影响；commits `72335a9` `bcace2a` `2520cdc` `f47f554` `8df7e46` `65a8c29` `a610723` 等 16 次提交；`pytest tests/` 共 **180 用例 green**（手测轮新增 ~10 条端到端回归）；`just lint` green、`complexity_guard regressions=0`（baseline 刷新若干次，因为 line-anchored fingerprint 受 docstring 行号变动影响）；`scripts/diag_keys.py` 落地作为未来"按键不反应"问题的诊断工具。
- Next: 进入 P1-M2（真实 / faux provider + AI stream contract）。M2/M3/M4 接入路径已多次确认：`PlaybackHarness` 走 controller 公开 event/control plane 两个面，可直接替换为 `Agent.events.subscribe()`，`InteractiveController` / `TUIApp` / 业务组件不需动。
- Risk: 无；M1 现在的"用户能跑通"状态比 acceptance 表本身更严格，进 M2 的依赖项全部就位。

## 2026-04-26 22:04 (local) | P1-M1 follow-up UX increments
- Status: done
- Done: 完成 `dev_docs/plans/p1_m1_followups.md` W1/W2/W3：anchored renderer 保留 shell history + 退出 prompt 新行；`TerminalSession.query_cursor_row()` / `TUIApp._prepare_anchor()` / `Renderer.set_anchor()` ownership 拆清；late DSR CPR 在 `StdinBuffer` 丢弃；新增 `TUIApp.schedule_callback` 与 `tui.components.spinner.Spinner`，现有 `Loader` / `CancellableLoader` 收敛到唯一 `PI_FRAMES`；新增 `Text` / `Spacer` / `Box` / `Container` / `TruncatedText` substrate primitives，`MessageListComponent` 改为 `Container` 薄壳。
- Evidence: `dev_docs/logs/p1_m1_closeout.md` § P1-M1 follow-up；ADR-0015 §影响 amended，`design_docs/decisions/INDEX.md` 记录 amendment，架构 TUI Contract primitive 列表与手测 §2 anchored renderer 期望已同步；`pytest tests/` **224 passed**；`just lint` green（`ruff check src/` passed，`complexity_guard regressions=0`）。
- Next: 继续进入 P1-M2；本 follow-up 未改变 `InteractiveController` event/control plane，也未改变 `PlaybackHarness` 路径。
- Risk: 真实 TTY DSR timeout / 不支持时会退化为屏底锚定并滚动当前 viewport，但 scrollback 保留；非 TTY / pipe / playback 不 DSR、不写 fallback newline，anchor=1。
