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
