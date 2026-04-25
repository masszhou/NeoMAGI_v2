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
