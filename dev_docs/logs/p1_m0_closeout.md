---
doc_id: 019dc5fa-8c81-713f-a834-39ff137b5f76
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T20:50:22+02:00
---
# P1-M0 Closeout

- Status: done
- Date: 2026-04-25
- Plan: `dev_docs/plans/p1_m0_pi_baseline_and_fixtures.md`
- Baseline: pi-mono `97a38bf6` (ADR-0011)
- 验收来源：plan §完成标准（acceptance）

## W0–W6 状态

| W | 工作项 | 状态 | 关键产出 |
| --- | --- | --- | --- |
| W0 | 包骨架 + lint gate | done | `pyproject.toml` `[build-system]=hatchling`、`pydantic>=2,<3` 进入生产依赖、7 个 `src/` 顶级包 + `__init__.py`；`justfile` 切换为 `infra.complexity_guard`；`uv sync` + `python -c "import ai_provider, agent_core, cli.core, cli.extensions, tui, storage, policy, infra"` 通过；`just lint` green，`complexity_guard` 0 regression |
| W1 | Behavior matrix | done | `design_docs/architecture/pi_behavior_matrix.md` A–I 9 大区块；ExtensionAPI 24 项方法 + EventBus 属性逐行枚举；每条 entry 引用 pi-mono 文件路径（含 line range） |
| W2 | 协议类型声明 (pydantic v2) | done | `src/ai_provider/types.py`、`src/agent_core/types.py`、`src/cli/core/session_types.py`、`src/cli/extensions/types.py`；按 ADR-0010 实施 ConfigDict + alias + TypeAdapter；`AgentEvent` 10 帧 / `AgentSessionEvent` 15 帧；4 个 coding 自定义 role 仅在 `cli.core` 声明；timestamp 双单位约束（ISO8601 str / Unix ms int）落到 fixture round-trip 测试 |
| W3 | Overflow + usage 常量 | done | `src/ai_provider/overflow.py` 20 条 OVERFLOW + 3 条 NON_OVERFLOW；`src/ai_provider/usage.py` 提供 `calculate_cost` 与 `normalize_provider_usage` 占位；`tests/test_overflow.py` 23 用例 green |
| W4 | 26 条兼容性 fixture | done | `tests/fixtures/pi_compat/<scene>/` 全 26 个目录就位；8 条核心交付完整 input + expected（assistant_text_delta / tool_execution_success / parallel_tools / compaction / cache_retention_none / overflow_error_patterns / silent_overflow / rpc_prompt_flow）；`tests/test_fixture_round_trip.py` 43 用例 green（含 8 条核心 fixture 的文件清单 acceptance），opaque 字段 + timestamp 类型保留双重断言通过 |
| W5 | Pi 基线文件索引 | done | `design_docs/architecture/pi_mono_baseline.md` 引用 ADR-0011，列出 `packages/ai/`、`packages/agent/`、`packages/coding-agent/`、`packages/tui/` 关键文件 + line range；与 behavior matrix 双向引用 |
| W6 | TUI playback 协议 | done | `design_docs/architecture/tui_playback_format.md` 明确 `events.jsonl` 仅 `AgentSessionEvent` / `AssistantMessageEvent`；harness 控制全部走 `playback.json` sidecar（version / scene / speed_multiplier / delays_ms / injects） |
| W7 | 进度归档 + closeout | done | `dev_docs/progress/progress.md` 追加 P1-M0 closeout 条目；本文件 |

PR / commit hash：M0 在主干一次性提交（详见随附 commit）。

## 验收检查

| 编号 | 验收条目 | 结果 |
| --- | --- | --- |
| 1 | `just lint` green，complexity ratchet 无回退 | ✅ `target=9`、`block=0`、`regressions=0` |
| 2 | `pyproject.toml` 含显式 hatchling build-system + 7 个顶级包 + 生产 pydantic；`uv sync` 成功；顶级 import 通过 | ✅ |
| 3 | `pi_behavior_matrix.md` 覆盖 A–I 9 区块，ExtensionAPI 24 + `events: EventBus` 全枚举 | ✅ |
| 4 | 4 份类型模块按 ADR-0010 暴露 pydantic v2 model + TypeAdapter；core 10 / session 15；4 个 coding role 仅在 `cli.core` | ✅ |
| 5 | `is_context_overflow` 对 16 个 provider sample 返回正确判定 | ✅ `pytest tests/test_overflow.py` 23 用例 |
| 6 | 26 个 fixture 目录就位；8 条核心 round-trip 通过 | ✅ `pytest tests/test_fixture_round_trip.py` 43 用例（含 8 条核心 fixture 文件清单 acceptance + round-trip + 字段语义） |
| 7 | `pi_mono_baseline.md` / `tui_playback_format.md` / `progress.md` 三份文档存在并入库；交叉引用就位；`events.jsonl` 是纯 events 流，控制位 sidecar | ✅ |

## 偏离与已知差异

- `pi_behavior_matrix.md` § A 报告 21 条内建 slash command（按 `97a38bf6` `slash-commands.ts:17–39` 实际计数），plan 文本内引用的"25 条"是估算值。本次以代码实际为准；后续 milestone 不应回退。
- `pi_behavior_matrix.md` § C 内建工具回归 pi-mono `tools/index.ts` 的实际划分：coding profile 4 条（`read` / `bash` / `edit` / `write`）+ read-only profile 4 条（`read` / `grep` / `find` / `ls`，`read` 共享）。M0 早期版本误把架构文档里的 `download` 当作 NeoMAGI 增量工具列入 §C，这次一并撤回；§I 把"禁止内建 `download` / 网络抓取工具"显式标注为项目理念 anti-feature。架构文档 §Built-In Tools / §Policy Contract 同步修订。
- 部分 fixture（如 `compaction`）的 `parentId: null` 在序列化时按 ADR-0010 `exclude_none=True` 规则被省略。fixture 已使用"absence = no parent"约定，与 NeoMAGI canonical 序列化一致。Pi-mono 原生 JSONL 不写 `id` / `parentId` 到 entry 字段，这是 NeoMAGI 自身的设计扩展。
- `agent_core.types.AgentToolResult.content` 暂时声明为 `list[dict[str, Any]]` 而非完整 union；M2 引入 provider adapter 时再改为 `TextContent | ImageContent` discriminated union（不影响 M0 fixture）。
- `ToolDefinition` 中的 `prepareArguments` / `execute` / 渲染回调为运行时 callable，未进入 wire 模型；M3 实现时再补上 ABC。

## Upstream observed but deferred

按 ADR-0011，开发期间发现的 pi-mono `97a38bf6` 之后的行为变化默认入 backlog；M0 期间未对 upstream 做诊断（fetch 时间 2026-04-25，后续未再 fetch）。下次 baseline 升级 ADR 必须显式 diff 以下区域：

1. `packages/ai/src/utils/overflow.ts` 的 `OVERFLOW_PATTERNS` / `NON_OVERFLOW_PATTERNS` 集合（NeoMAGI 已固化为 20 + 3 条；任何上游修改必须列入升级 ADR）。
2. `packages/coding-agent/src/core/extensions/types.ts` 的 `ExtensionAPI` 方法签名与事件 result shape（NeoMAGI behavior matrix § D / E 直接引用其行号）。
3. `packages/coding-agent/src/core/messages.ts` 4 个 coding 自定义 role 的字段（NeoMAGI `cli.core.session_types` 与之 1:1）。
4. `packages/coding-agent/src/core/compaction/compaction.ts` 默认值 `reserveTokens=16384` / `keepRecentTokens=20000`（NeoMAGI behavior matrix § H 直接引用）。
5. `packages/coding-agent/src/modes/rpc/rpc-types.ts` 的 30 条 RPC 命令 union（NeoMAGI behavior matrix § B 与 fixture `rpc_prompt_flow` / `rpc_sync_response` 直接引用）。

如果未来某次 fetch 发现这些区域有变动，按 ADR-0011 §影响段先开升级 ADR + diff review，再修改本仓库 fixture / matrix / 类型。

## M1 前置条件检查

| 项 | 状态 | 备注 |
| --- | --- | --- |
| `cli.core.session_types.AgentSessionEvent` 15 帧可被 TypeAdapter round-trip | ✅ | M1 mock harness 直接消费 |
| `tui_playback_format.md` 协议落地 | ✅ | M1 实现 mock harness 时按本协议读取 events.jsonl + sidecar |
| `tests/fixtures/pi_compat/assistant_text_delta/events.jsonl` 等 8 条核心 fixture 可作为 M1 输入 | ✅ | 不需要 inject 时 sidecar 留空 |
| `pi_behavior_matrix.md` § F 列出的 TUI 设置默认值 | ✅ | M1 settings 实现时引用本文件，不再单独决定默认值 |
| `complexity_guard` baseline 干净 | ✅ | 0 regression；M1 启动前如有大批新代码，可在 PR 内单独刷一次 baseline |
| `just md-doc-header` 已对 M0 新增的 markdown 文件应用 | ✅ | 本文件 + behavior matrix + tui playback + pi_mono_baseline + closeout 全部已加 doc_id |

## 后续移交（plan §后续移交）

- M1：`tui_playback_format.md`、`assistant_text_delta` / `tool_execution_success` / `parallel_tools` fixture、`AgentSessionEvent` 15 帧 + `AssistantMessageEvent` 12 帧。
- M2：`overflow.py` + `usage.py`、`cache_retention_none` / `usage_cache_normalization` fixture、`Model` / `ProviderConfig` 类型。
- M3：`agent_core.types.AgentEvent`、`cli.extensions.types.ExtensionAPI` Protocol、`ToolDefinition` wire shape、剩余 18 条 fixture（M3 实现对应能力时与代码同 PR 提交）。
- M5：`rpc_prompt_flow` / `rpc_sync_response` fixture、`packages/coding-agent/src/modes/rpc/rpc-types.ts` 完整 30 条命令；按 behavior matrix § B 落地。
- M6：`cli.core.session_types.SessionEntry` 9 类 + Postgres schema（architecture line 530–627）。
- M8 / M9 / M10：`pi_behavior_matrix.md` § A–F 用作产品验收清单。
