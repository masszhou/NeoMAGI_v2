---
doc_id: 019dc5f9-1db2-762d-b462-aa43acf7cc9f
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T20:48:47+02:00
---
# TUI Mock Playback Format (P1-M0)

- Status: accepted
- Date: 2026-04-25
- Architecture: `design_docs/architecture/p1_pi_cli_technical_architecture.md`
  § P1 Implementation Acceptance (line 1158)
- Plan: `dev_docs/plans/p1_m0_pi_baseline_and_fixtures.md` § W6
- Consumers: M1 mock harness (drives the TUI without an agent runtime),
  M2/M3 runtime tests (replay events without harness control).

## 1. 设计原则

P1 acceptance 硬性要求：**TUI mock playback 只消费 `AgentSessionEvent` 与
`AssistantMessageEvent`**。这意味着事件流和 harness 控制必须分离，否则 TUI 实
现会被迫识别非协议字段，破坏 fixture round-trip 与运行时事件流的字节级一致性。

因此本协议把每个 fixture 拆成两份文件：

- `events.jsonl` —— 唯一的事件流，TUI 真正消费的内容；
- `playback.json` —— 同目录可选 sidecar，仅 M1 mock harness 读取，不进入 TUI。

M2 / M3 runtime 测试可以单独 replay `events.jsonl` 而不读 sidecar；harness
也不能把控制信息夹塞进事件 JSON 里。

## 2. `events.jsonl`

每行严格是一个 `AgentSessionEvent` 或 `AssistantMessageEvent` JSON 对象。

约束：

- 不允许任何元数据、harness 时序字段或 `_*` 前缀的私有键。
- 每行末尾使用单个 `\n`，不是 `\r\n`（与 Pi RPC framing 对齐：`split only on
  \n; strip optional trailing \r`）。
- 事件顺序就是 TUI 看到的顺序；harness 注入的副作用通过 sidecar 的 index 关
  联，而不是穿插在事件流里。
- 即便 fixture 是 `AssistantMessageEvent` 流（M2 stream-only fixture），TUI
  也不需要单独识别两个 union——M1 实现层可以让 `AgentSessionEvent` 的
  `message_update` 携带任意 `AssistantMessageEvent`，与 architecture line 348
  保持一致。

合法事件类型见：

- `agent_core.types.AgentEvent`（10 帧 core 事件）
- `cli.core.session_types.AgentSessionEvent`（追加 5 帧 session-level：
  `queue_update` / `compaction_start` / `compaction_end` / `auto_retry_start` /
  `auto_retry_end`，共 15 帧）
- `ai_provider.types.AssistantMessageEvent`（12 帧 stream 事件）

任何 `events.jsonl` 行都必须能通过对应 `TypeAdapter` round-trip（fixture
round-trip test 直接对每行做 `validate_python` + `dump_python(by_alias=True,
exclude_none=True)`）。

## 3. `playback.json`（sidecar）

可选。仅当 M1 mock harness 需要节奏控制或副作用注入时存在。Schema：

```json
{
  "version": 1,
  "scene": "<fixture_directory_name>",
  "speed_multiplier": 1.0,
  "delays_ms": [0, 50, 50, 50, 50, 0],
  "injects": [
    {"after_event_index": 3, "action": "abort"},
    {"after_event_index": 5, "action": "user_input", "text": "stop"}
  ]
}
```

字段：

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `version` | int | 是 | 当前固定为 `1`。后续不兼容修改必须升版本号。 |
| `scene` | str | 是 | 与 fixture 目录名一致，便于交叉校验。 |
| `speed_multiplier` | float | 否 | 默认 `1.0`；harness 在投递每帧前用此值乘以 `delays_ms[i]`。 |
| `delays_ms` | list[int] | 否 | 与 `events.jsonl` 行号一一对应；缺省视为全 `0`。长度必须等于 `events.jsonl` 行数。 |
| `injects` | list[Inject] | 否 | 在指定 event 投递**之后**触发的 harness 副作用。 |

`Inject` 形状：

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `after_event_index` | int | 是 | 0-based 行号，触发副作用的事件位置。 |
| `action` | str | 是 | `abort` / `user_input` / `resize` / `quit`。M1 起点支持 `abort`；其余按需扩展。 |
| `text` | str | 否 | `user_input` 时的输入字符串（包含换行视为提交）。 |
| `width` / `height` | int | 否 | `resize` 时的新终端尺寸。 |

## 4. M1 mock harness 行为

1. 加载 `events.jsonl`，逐行 `validate_python` 校验；任何一行失败立刻抛错并
   终止 playback。
2. 加载同目录 `playback.json`（缺失则使用 `version=1, delays_ms=[0]*N,
   injects=[]` 的默认）。
3. 从 `i = 0` 起：
   - 等待 `delays_ms[i] * speed_multiplier` 毫秒；
   - 把 `events.jsonl[i]` 投递给 TUI；
   - 执行 `injects` 中所有 `after_event_index == i` 的 action；
4. 全部投递完毕后等 TUI 进入 idle，再退出。

## 5. M2 / M3 runtime 用法

- 直接读 `events.jsonl`，按 `AgentSessionEvent` / `AssistantMessageEvent` 校
  验流的有效性（顺序、`done` / `error` 终结、`start` 必先发等）。
- 不读 sidecar，因此 runtime 测试不会被 harness 时序污染。
- M2 faux provider 与 M3 agent loop 应能从同一份 `events.jsonl` 重放出与
  fixture 一致的输出，这是 cross-layer 字节级 contract 的根证据。

## 6. 失败模式

| 错误 | 检测方式 | 处理 |
| --- | --- | --- |
| `events.jsonl` 行不通过 TypeAdapter | round-trip test | 立刻报错，停止 playback / 测试 |
| sidecar `version != 1` | harness 启动时 | 拒绝运行；提示更新 harness |
| `delays_ms` 长度与事件数不一致 | harness 启动时 | 拒绝运行；fixture 修复后再跑 |
| `inject.after_event_index` 越界 | harness 投递前 | 跳过该 inject 并打印 warning |
| `events.jsonl` 包含 `_*` 私有键或非协议字段 | round-trip test 失败 | 视为 contract 违例 |

## 7. 与 fixture 目录的关系

- `tests/fixtures/pi_compat/<scene>/events.jsonl` 是 W4 fixture 的事件流文件。
- 当某个 fixture 同时给 M1 提供 mock harness 输入时，会在同目录写
  `playback.json` sidecar（M0 的 8 条核心 fixture 中没有 inject 需求时，
  sidecar 缺省即可）。
- M1 实现的 mock harness 不能向 fixture 目录写入任何运行时文件；产物（如
  录屏 / 终端 dump）必须落到 `tmp/` 或测试临时目录。
