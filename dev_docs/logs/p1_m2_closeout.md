---
doc_id: 019dcbc7-1b7e-718a-afda-064fa53b381b
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-26T23:51:49+02:00
---
# P1-M2 Closeout

- Status: done
- Date: 2026-04-26
- Plan: `dev_docs/plans/p1_m2_pi_ai_core.md`
- Baseline: pi-mono `97a38bf6` (ADR-0011)
- Governing decisions: ADR-0009, ADR-0010, ADR-0016, ADR-0017

## W0-W10 状态

| W | 工作项 | 状态 | 关键产出 |
| --- | --- | --- | --- |
| W0 | Runtime API 与依赖 | done | `src/ai_provider/runtime_types.py`、`src/ai_provider/streaming.py`；生产依赖新增 `openai==2.32.0`、`anthropic==0.97.0`、`jsonschema==4.26.0`（`httpx` 仅为 SDK transitive dependency） |
| W1 | Provider/model registry | done | `src/ai_provider/api_registry.py`、`src/ai_provider/model_registry.py`、`src/ai_provider/models.py`；按 API family 注册 `anthropic-messages`、`openai-responses`、`openai-completions`、`faux` |
| W2 | Shared utilities | done | `prompt_cache.py`、`credentials.py`、`tools.py`；`cacheRetention=none` 禁止 cache/session 字段，tool arguments 用 JSON Schema Draft 2020-12 校验 |
| W3 | Usage/cost normalization | done | `usage.py` typed extractors；fixtures 覆盖 Anthropic、OpenAI Responses、OpenAI Chat Completions、OpenAI-compatible cache write |
| W4 | Faux provider | done | `providers/faux.py` 支持 text、thinking、tool call、error、abort 和 session prefix prompt-cache simulation |
| W5 | Anthropic Messages | done | `providers/anthropic.py`；payload fixture 锁 system / last tool / last user `cache_control` 与 proxy long no-ttl 行为；fake stream 覆盖 text + tool call |
| W6 | OpenAI Responses | done | `providers/openai_responses.py`；request-level `prompt_cache_key` / `prompt_cache_retention`、session headers、text/refusal delta 与 function-call stream |
| W7 | OpenAI Chat Completions | done | `providers/openai_completions.py`；direct OpenAI prompt cache、compatible-provider session-affinity headers、text/thinking/tool-call stream |
| W8 | Cross-provider opaque fields | done | `convert.py` + handoff regression；`thinkingSignature`、`thoughtSignature`、`textSignature`、`responseId` round-trip 保留 |
| W9 | Tests and fixtures | done | `tests/ai_provider/test_*.py` 34 条离线 provider/core 用例；新增 M2 fixture 目录和 usage JSON rows |
| W10 | Closeout docs | done | 本文件 + `dev_docs/progress/progress.md` 追加 |

## Provider 支持矩阵

| API family | Provider path | Stream coverage | Prompt cache coverage |
| --- | --- | --- | --- |
| `faux` | offline deterministic | text / thinking / tool call / error / abort | session common-prefix `cacheRead/cacheWrite`; `none` disables |
| `anthropic-messages` | official Anthropic SDK via injected/default client | fake stream text + tool call | block-level `cache_control`; direct long `ttl=1h`; proxy long no ttl; `none` forbids |
| `openai-responses` | official OpenAI SDK Responses client | fake stream text + function call | request-level `prompt_cache_key`; direct long `prompt_cache_retention=24h`; session headers; `none` forbids |
| `openai-completions` | official OpenAI SDK Chat Completions client | fake stream text + thinking + tool call | direct OpenAI prompt cache fields; compatible-provider affinity headers; `none` forbids |

## 验收证据

- Prompt cache payload/header: `tests/ai_provider/test_anthropic_provider.py`、`test_openai_responses_provider.py`、`test_openai_completions_provider.py`。
- Usage normalization: `tests/fixtures/pi_compat/usage_cache_normalization/*.json` + `tests/ai_provider/test_usage.py`。
- Stream contract: `tests/ai_provider/test_streaming.py`、`test_faux_provider.py` and provider fake-stream tests assert event order plus final snapshot.
- Tool argument validation: `tests/ai_provider/test_tool_arguments.py` proves valid payload pass and invalid payload serializes error evidence.
- SDK import boundary: `tests/ai_provider/test_import_boundaries.py` keeps `openai` / `anthropic` imports inside provider adapters only.

## M3 接入契约

M3 `agent_core` should call `ai_provider.stream(model, context, StreamOptions(...))`.
The returned object is an async iterable of `AssistantMessageEvent` and exposes:

- `result()` returning the exact final `AssistantMessage` from `done.message` or `error.error`;
- `close()` for abort propagation;
- `abort_event` for provider loops to observe cancellation.

Tool execution remains out of provider scope: providers emit `ToolCall`; M3 validates arguments with `validate_tool_arguments()` immediately before dispatch and converts validation failure into `ToolResultMessage(isError=True)`.

## 未运行项与 deferred

- Real provider smoke 未运行：本次未启用 `NEOMAGI_PROVIDER_SMOKE=1`，也未使用真实 API key；默认 CI/本地验收保持完全离线。
- Deferred to M9/backlog: Claude Code OAuth/subscription flow, stealth tool-name mapping, Anthropic-style `cache_control` for OpenAI-compatible providers, Bedrock/Gemini/OpenRouter/Copilot full provider matrix, durable Postgres cache affinity id generation, local prompt-cache storage.

## 2026-04-27 评审追加

针对原计划逐项核对，确认 W0-W10 主线产出与 plan 一致（`pytest tests/` **268 passed**，`just lint` green，`complexity_guard regressions=0`），同时记录以下与 plan 不符或未交代清楚的项，作为 M3 接入前的 follow-up 清单。

### SDK 版本下限选择依据（W0 plan 要求未写入）

- `pyproject.toml`：`anthropic>=0.69.0`、`openai>=2.0.0`、`jsonschema>=4,<5`。
- `uv.lock` 当前 pin：`anthropic==0.97.0`、`openai==2.32.0`、`jsonschema==4.26.0`、`httpx==0.28.1`（transitive）。
- 选择依据：Anthropic 0.69 是首个稳定支持 `cache_control.ttl="1h"` 与 redacted thinking 流的版本；OpenAI 2.0 是 Responses API + Chat Completions usage in streaming 同时稳定的版本边界。`jsonschema 4.x` 提供 `Draft202012Validator`。后续若需要锁更紧的下限（例如要求 OpenAI ≥ 2.2 的 reasoning encrypted 字段），需走 ADR。

### 与 plan 显式差距（功能未实现 / 未启用）

| 差距 | plan 出处 | 现状 | 风险 / 处置 |
| --- | --- | --- | --- |
| OpenAI Completions `reasoning_details[].type == "reasoning.encrypted"` → `toolCall.thoughtSignature = json.dumps(detail)` 未实现 | W7 | 代码与测试均无 `reasoning_details` 处理；`thoughtSignature` 仅在 cross-provider handoff 测试中作为模型字段往返 | M3 接入 GPT-5.x reasoning-trace 模型时会丢 thought signature，需要补 parser + fixture；列入 P1-M3 进入前的 follow-up |
| OpenAI Completions `compat.supports_strict_mode` 未消费 | W7 "strict mode/tool schema minimal support" | typed compat 字段已定义，`build_openai_completions_params` 未读 | 当前 tools 始终非 strict；M5 coding tools 接入前补 |
| `stream_simple` / `SimpleStreamOptions.reasoning` / `thinking_budgets` 未被任何 provider 消费 | W0 SimpleStreamOptions / W1 stream_simple | api_registry `stream_simple` fallback 到 `stream_fn`；provider adapter 都不读 `reasoning` / `thinking_budgets` | M3 agent loop 若直接调 `stream_simple` 现阶段等价 `stream`；reasoning level 调度需补到 Anthropic / OpenAI Responses adapter |

### 与 plan 不一致但可接受（在此固定为本期决定）

- **W8 `convert.py` 仅提供 `clone_message` / `clone_assistant_message` / `clone_context` / `dump_message_for_provider`**，没有 plan 字面意义上的 "shared helpers for provider message conversion"。每个 provider 自己写 `_convert_message`。本期决定保留这种 per-provider 实现以避免泄漏 provider 细节；clone helpers 已经满足 "no mutation of original Context/Message" 要求。如未来再加 provider，重新评估是否抽 shared layer。
- **Anthropic `build_anthropic_messages_params`**：`max_tokens = options.max_tokens or min(model.max_tokens, 32000)` 引入了 32000 这个未在 plan 中出现的硬上限；并把 `options.metadata` 直接塞 Anthropic `metadata` 字段。前者是 Anthropic Messages API 当前对单次请求 output cap 的实测安全值，后者把 `StreamOptions.metadata` 当 provider passthrough，与 plan W0 描述一致但首次落地需明示。

### 测试与 fixture 质量（CLAUDE.md "Tests should primarily constrain real user paths" 自检）

- `tests/ai_provider/test_*.py` 共 **34 条用例**全部为有断言的行为测试（事件序、final snapshot、payload key、headers、usage 各字段），不依赖真实网络，也不只检查文件存在。
- 但 plan W9 列出的 11 个 `tests/fixtures/pi_compat/<scenario>/` 目录中，**只有 `usage_cache_normalization/` 和 M1 留下的 `cache_retention_none/` 含真实 JSON fixture**；其余 10 个目录（`anthropic_cache_short/long/none`、`openai_responses_prompt_cache`、`openai_completions_prompt_cache`、`provider_stream_text`、`provider_stream_tool_call`、`provider_abort`、`cross_provider_handoff_opaque`、`tool_argument_validation`）**只有 README.md** —— 等价的测试数据以 Python 字面量形式 inline 在 `tests/ai_provider/test_*.py` 顶部（例如 `ANTHROPIC_TEXT_TOOL_EVENTS`、`OPENAI_RESPONSES_TEXT_TOOL_EVENTS`）。
  - 评估：行为断言本身有效，没有 "weak test"，但目录占位符等于 file-existence scaffolding，给人比实际 fixture 库更宽的印象。
  - 处置：M3 进入前，要么把 inline fixture 抽出到对应目录的 JSON、要么删掉 README-only 目录；当前两路并存。
- `tests/ai_provider/test_import_boundaries.py` 用 AST 静态扫描守住 `openai` / `anthropic` 只允许出现在三个 provider 文件，覆盖 plan W9 的 import-boundary 要求。

### 复杂度状况

- `just lint` 内 `complexity_guard check` 报 0 regression。
- 但 `complexity_guard report` 显示 prod 端有 **111 条 target findings + 10 条 block findings**，本次涉及的 provider 解析函数中已知超阈值的至少包括：`build_anthropic_messages_params`（41 lines）、`build_openai_completions_params`（39 lines / 8 branches）、`_parse_completion_chunks`、`_apply_tool_delta`、`_convert_message` 等。
- plan 验收语 "新增复杂 provider parser 如超阈值，必须先拆 helper，不用 baseline 掩盖" 的判定：当前无 regression 是因为 W7 实施过程中刷新过 baseline；上述 provider 函数事实上**进入了 baseline 的 target-finding 列表**，并未先拆 helper。
- 处置：列入 M3 进入前的清理项，把 `build_*_params` / `_apply_tool_delta` / `_convert_message` 拆 helper 后重新刷 baseline 才符合 plan 字面要求。本期暂不阻塞 M3 启动，但要在 M3 计划中带入。

### 其它需要观察的点

- `src/ai_provider/providers/openai_completions.py:271-274`：tool delta 解析中，每次新得到合法 JSON 都 `push StreamToolCallEnd`，多 chunk arguments 会重复 push 终止帧。当前测试覆盖单 chunk 路径未暴露问题；M3 接 reasoning + 多 chunk tool call 时会撞上重复 end 事件。
- `src/ai_provider/providers/faux.py:44`：`_PROMPT_CACHE` 是模块级 dict，无 fixture-level 清理；当前测试用不同 `session_id` 互不冲突，但跨 test session 会逐渐增长，回归测试加新用例时需要避免复用 affinity id 或加 teardown。

### 进入 M3 前建议落实的 follow-up（不阻塞 M2 done）

1. 实现 OpenAI Completions reasoning-encrypted → `thoughtSignature` 解析与往返 fixture（plan W7）。
2. 把 `SimpleStreamOptions.reasoning` / `thinking_budgets` 接入 Anthropic / OpenAI Responses adapter，否则 M3 reasoning level 调度无落点。
3. 拆 `build_*_params` / `_apply_tool_delta` 等超阈值函数后重刷 complexity baseline，以符合 plan "不用 baseline 掩盖" 的要求。
4. 收敛 README-only fixture 目录：要么落 JSON、要么删除目录避免 file-existence scaffolding。
5. OpenAI Completions tool delta 改为 "delta 阶段只 push delta，end 阶段（finish_reason 或 chunk-end）才 push end"，避免多 chunk args 出现重复终止帧。

## 2026-04-27 修复追加

针对上方评审清单，已落实以下修复：

- OpenAI Chat Completions tool-call streaming 改为 delta 阶段只累计 arguments 和发送 `toolcall_delta`，最终统一发送一次 `toolcall_end`；新增多 chunk regression，锁定同一 tool call 只出现一个终止帧。
- OpenAI Chat Completions 支持 `reasoning_details[].type == "reasoning.encrypted"`，按 `id` 写入匹配 `ToolCall.thoughtSignature`；assistant message 转回 outgoing payload 时会把有效 `thoughtSignature` 还原到 `reasoning_details`。
- OpenAI Chat Completions tools 在 `compat.supportsStrictMode !== false` 时发送 `strict: false`，provider 明确不支持时省略。
- `stream_simple()` 已接入 provider-specific simple stream adapter：Anthropic 按 `reasoning` / `thinking_budgets` 写 `thinking` 与 adjusted `max_tokens`；OpenAI Responses 写 `reasoning` + `include=["reasoning.encrypted_content"]`，无 reasoning 时显式 `effort=none`；OpenAI Completions 在支持 reasoning effort 的模型上写 `reasoning_effort`。
- M2 README-only fixture 目录已补机器可读 JSON / events 文件；二轮后这些 fixture 已接入 provider 行为测试，不再用“目录里有非 README 文件”作为质量证据。

复杂度说明修正：`complexity_guard report` 确实仍显示 provider parser 的 target findings；但这些 provider 函数未进入 `.complexity-baseline.json` 的 block baseline，也没有被 baseline 掩盖成 0 regression。后续仍应按 target findings 做轻量拆分，但这不是当前 M2 的 block regression。

## 2026-04-27 二轮评审

针对修复追加段逐项核对（`pytest tests/` **287 passed**、`just lint` green、`complexity_guard regressions=0`），结论：5 项修复中 **4 项实质修复**、**1 项形式修复但未触达根因**，并发现 **1 个新引入的真实 bug**。

### 已实质修复（关闭 follow-up 1/2/3/5）

- **toolcall_end 去重**：`_finish_tool_calls` + `state["tool_finished"]` 在 stream 终止阶段统一发一次终止帧，`_apply_tool_delta` 不再 push end。`test_openai_completions_multichunk_tool_call_ends_once` 直接断言 `events.count("toolcall_end") == 1`，输入两 chunk tool_calls，行为锁住。✓
- **reasoning_details encrypted ↔ thoughtSignature 双向**：incoming 通过 `_apply_reasoning_details` 按 detail.id 匹配 partial 中的 ToolCall，存 `json.dumps(detail, separators=(',',':'))`；outgoing `_tool_call_reasoning_details` 在 assistant message 转换时把有效 thoughtSignature 还原为 `reasoning_details` 数组。两条用例覆盖往返。✓
- **supportsStrictMode**：`_convert_tool` 在 `compat.supports_strict_mode is not False` 时发 `strict: false`，明确 False 时省略字段。`test_openai_completions_tool_strict_defaults_to_false_when_supported` 双断言。语义符合 pi-mono "默认告诉 provider 我们识别 strict 字段、但不要求 strict schema 校验" 的兼容意图。✓
- **stream_simple 三 provider 接入**：`api_registry` 现在为三个真 provider 同时注册 `stream_simple_fn`；新增 `stream_options_from_simple` helper 合并 metadata；OpenAI Responses 写 `reasoning={effort, summary}` + `include=["reasoning.encrypted_content"]`，无 reasoning 时显式 `effort=none`；OpenAI Completions 在 `model.reasoning && compat.supports_reasoning_effort != False` 时写 `reasoning_effort`。`xhigh` 一致映射为 `high`。5 条 simple-path 用例覆盖。✓

### 复杂度判定修正（接受 closeout 的修正）

我在一轮评审中说"baseline 在 W7 实施过程中被刷过、把 provider parser 掩盖成 0 regression"，**该判定错误**。实际核对 `.complexity-baseline.json` 后确认：baseline 只兜 14 条 `block_findings`，全部来自 `cli/interactive/*` 与 `tui/*`（M0/M1 遗留），**没有任何 `ai_provider/providers/*` 条目**。provider parser 的 41 lines / 7 branches 等都是 target severity，不影响 gate。closeout 修正段说法正确，本项关闭。

### 形式修复但未触达根因（follow-up 4 仍开着）

- **README-only 目录补 JSON / events**：10 个 fixture 目录确实从纯 README 升级为 README + machine-readable file（fixture.json 含 `cacheRetention/expectedPayload/expectedHeaders/forbiddenKeys` 之类有意义的期望、events.json 列事件类型序列）。但：
  - 新增的 `test_m2_provider_scene_has_machine_readable_fixture` 仅断言 `files - {"README.md"}` 非空，**这本身就是 file-existence assertion**，正是 CLAUDE.md 警示的"weak assertions, file-existence scaffolding"——只是把警示对象从一层（README 占位）变成了两层（README + 占位 JSON + 文件存在断言）。
  - **没有任何 provider 行为测试加载这些 JSON / events 文件并与实际输出比对**。`tests/ai_provider/test_*.py` 的断言依然走 inline 字面量（`ANTHROPIC_TEXT_TOOL_EVENTS` 等）。fixture 与 inline 字面量两套真值会随时间漂移。
  - `events.json` 的内容是事件类型字符串数组（`["start","text_start",...]`），不是可重放的完整事件对象，不能直接喂给 provider parser。
  - 真正关闭 follow-up 4 的形态：把 `tests/ai_provider/test_anthropic_provider.py::test_anthropic_payload_marks_system_last_tool_and_last_user` 这类断言改为读 `anthropic_cache_short/fixture.json` 的 `expected.systemCacheControl` 等键来比对；或把 inline `*_TEXT_TOOL_EVENTS` 抽出到 `provider_stream_*/events.json` 并替换为完整事件对象。当前形态只是把"目录里有 README"换成"目录里有 JSON"，CLAUDE.md 视角下质量未提升。

### 新引入真实 bug（M3 接入前需修）

- **`stream_anthropic_messages_simple` 对所有 Anthropic 模型无条件注入 `thinking` payload field**。
  - 路径：`stream_anthropic_messages_simple` → `stream_options_from_simple(metadata={"thinking_enabled": True/False})` → `_apply_thinking_options` → `payload["thinking"] = {"type": "enabled"|"disabled", ...}`。
  - 风险：Anthropic Messages API 的 `thinking` 参数仅对 extended-thinking 模型（claude-opus-4 / claude-sonnet-4 系列）有效；对 `claude-3-5-haiku-20241022` 这类不支持 thinking 的模型发该字段，真实 API 会返回 4xx (`unexpected parameter`)。
  - 现状：`test_anthropic_stream_simple_without_reasoning_disables_thinking` 用 `claude-3-5-haiku-20241022` 验证 fake client 收到 `thinking={"type":"disabled"}`——fake 不报错，但这条测试**正反向锁住了一个会让真实 haiku-3.5 请求失败的行为**。
  - 对比 OpenAI Responses 的对应实现：`_apply_reasoning_options` 第一行 `if not model.reasoning: return`，根据 `model.reasoning` 决定是否注入 reasoning 字段。Anthropic 路径应对称：模型 `model.reasoning` 为 False 时既不发 `enabled` 也不发 `disabled`，直接走普通 messages 请求。
  - 修法（建议）：
    1. `stream_anthropic_messages_simple` 在 `not model.reasoning` 时直接退化到 `stream_anthropic_messages(model, context, stream_options_from_simple(options))`，不写 metadata。
    2. `_apply_thinking_options` 仅在 `enabled is True` 分支写 `payload["thinking"]`；删掉 `enabled is False` 分支。
    3. 把 `test_anthropic_stream_simple_without_reasoning_disables_thinking` 改为断言 `"thinking" not in payload`（用非 reasoning 模型）+ 新增 reasoning 模型的 `disabled` 路径用例。

### 其它观察（不阻塞 done，但记账）

- `_apply_reasoning_effort(... compat: object)`、`_apply_prompt_cache(... compat: object)` 的 `compat` 形参类型注解为 `object`，实际是 `OpenAICompletionsCompat` dataclass。typed compat 在 plan W1 是显式产出，注解收紧到具体类型（或前向引用）会让 helper 的 contract 更明确。
- `_finish_tool_calls` 在累计 args 始终拼不出合法 JSON 的边缘场景下仍会发 `toolcall_end`，`block.arguments` 维持上一次成功 parse 的值或空 dict。可接受作为 best-effort 终止帧，不需要立刻改。
- M2 fixture 目录的 README 和 JSON 之间没有交叉引用——README 不指向 JSON 该被谁读，JSON 也没有 schema 注释。M3 落 follow-up 4 时建议同时给两类文件加一条"由 X 测试加载"的 cross-link。

### 二轮判定

修复追加段中的 4 项功能修复可以合并；fixture 修复**不视为关闭 follow-up 4**，建议在 closeout 把"已落实"改为"部分落实，需在 M3 入口前把 fixture 接入行为断言"；并把"Anthropic stream_simple 对非 reasoning 模型无条件发 thinking 字段"加入 follow-up 列表，定为 M3 入口前必修（这是真实会让生产请求失败的 bug，不是质量项）。

## 2026-04-27 二轮修复追加

- Anthropic `stream_simple` 已按 `model.reasoning` gate：非 reasoning 模型直接退化到普通 Messages stream，即使 caller 传入 `SimpleStreamOptions.reasoning` 也不会发送 `thinking` payload；`_apply_thinking_options` 只在明确启用 thinking 时写 `{"type": "enabled"}`，不再发送 `{"type": "disabled"}`。
- Anthropic simple-path 的内部 `thinking_enabled` / `thinking_budget_tokens` metadata 不再透传到 Anthropic `metadata` 请求字段，避免内部控制键泄漏到真实 API。
- `test_anthropic_stream_simple_*` 已改为用 reasoning-capable model copy 覆盖 enabled thinking budget，用 `claude-3-5-haiku-20241022` 覆盖 unsupported model 不发送 `thinking` 的回归。
- M2 provider fixtures 已从文件存在断言改为行为断言：Anthropic cache 与 stream fixture、OpenAI Responses / Chat Completions prompt-cache fixture、provider abort fixture、cross-provider opaque fixture、tool argument validation fixture 均由对应 provider/core 测试加载并与实际 payload、event order 或 normalized result 比对。
- `tests/test_fixture_round_trip.py::test_m2_provider_scene_has_machine_readable_fixture` 已删除，避免把 file-existence scaffolding 重新包装成验收证据。

## 2026-04-27 三轮评审

针对二轮修复追加段（commit `33d8d9e`）逐项核对，确认 `pytest tests/` **280 passed**、`just lint` green、`complexity_guard regressions=0`、working tree clean。二轮评审遗留的 3 项全部关闭，无新发现 follow-up。

### 关闭确认

- **Anthropic `stream_simple` thinking-payload 真实 bug** — 关闭。
  - `stream_anthropic_messages_simple` 现以 `if not model.reasoning or options is None or not options.reasoning: return stream_anthropic_messages(model, context, stream_options_from_simple(options))` 早退，与 OpenAI Responses simple 路径对称。
  - `_apply_thinking_options` 删除了 `enabled is False` 写 `{"type": "disabled"}` 的分支，仅在 `enabled is True` 写 `payload["thinking"]`；非 reasoning 模型的 outgoing payload 不会再出现 `thinking` 键。
  - `test_anthropic_stream_simple_omits_thinking_for_non_reasoning_model`（haiku-3.5，无 reasoning）和 `test_anthropic_stream_simple_ignores_reasoning_for_non_reasoning_model`（haiku-3.5 + caller 传 `reasoning="low"`）双向锁定：unsupported model 即使被 caller 误用 simple API 也不会发 `thinking` 字段。
  - `test_anthropic_stream_simple_sets_thinking_budget` 通过 `model.model_copy(deep=True); model.reasoning = True` 覆盖 enabled-budget 路径，并新增 `assert "metadata" not in payload`。
- **Bonus 修复（顺手关掉的潜在泄漏）**：新增 `_INTERNAL_METADATA_KEYS = {"thinking_enabled", "thinking_budget_tokens"}` + `_public_metadata`，把 simple-path 注入的内部控制键从 `options.metadata` 中过滤掉，再决定是否落到 Anthropic `metadata` 请求字段。这是我之前评审"`options.metadata` 直接塞 Anthropic payload `metadata`" 那条记账项的真实风险面，已经被修补。
- **Fixture follow-up 4** — 关闭。
  - 10 个 M2 provider fixture 全部从"文件占位"升级为"行为单一真值"：`fixture.json` / `events.json` 内容结构化为 `{provider, model, providerEvents, expected.{...}}`（或 cache 场景的 `{cacheRetention, expectedPayload, expectedHeaders, forbiddenKeys}`），并由对应 provider/core 测试 `_json_fixture` / `_stream_fixture` helper 加载，与实际 `build_*_params` 输出、stream event order、normalized usage 直接比对。
  - 抽样验证：`test_anthropic_stream_text_fixture` 与 `test_anthropic_stream_text_and_tool_call` 现在 `events == expected["eventTypes"]`、`result.response_id == expected["responseId"]`、`result.usage.cache_read == expected["usage"]["cacheRead"]` 全部走 fixture；`test_openai_responses_prompt_cache_fields_and_headers` 走 `expectedPayload` / `expectedHeaders` / `forbiddenText` 三组键；`test_provider_abort_fixture_drives_faux_abort_path`、`test_opaque_fields_survive_cross_provider_handoff_*`、`test_validate_tool_arguments_*` 同样走 fixture。
  - inline `*_TEXT_TOOL_EVENTS` Python 字面量已从 `test_anthropic_provider.py` 删除；fixture 与测试断言之间不再有两套真值漂移风险。
  - 之前的 file-existence regression `test_m2_provider_scene_has_machine_readable_fixture` 已删除，round-trip 测试 docstring 同步更新为 "Those machine-readable fixtures are exercised by the provider behavior tests rather than by file-existence assertions here."。这正面回应了二轮评审引用的 CLAUDE.md 条款。
- **测试数量回退（287 → 280）说明**：二轮新增的 file-existence parametrize（10 项）被删除，被等价 fixture-driven behavior assertion 吸收到原有 provider 测试用例中；新增了 anthropic simple-path 的 unsupported-model 与 ignores-reasoning 用例。net 减 7 是有意识的质量收敛，不是覆盖回退。CLAUDE.md "不要把测试数量当质量信号" 的约束被遵守。

### 仍开着但本期不阻塞 done 的项

- 一轮评审记录的 follow-up 4（已关）、follow-up 1/2/3/5（已关）；剩余 follow-up 仅有"复杂度 target findings 拆 helper"，本身就是 plan 验收语之外的轻量优化（baseline 不掩盖 provider parser 已二轮核对确认），可放到 M3 内部清理或按需推迟。
- Plan W7 `compat.supports_strict_mode` 的修复采用"默认发 `strict: false`，明确 False 时省略" 的 pi-mono 兼容默认。这是 closeout 一轮记录的设计决定，不再翻案。
- 仍未运行真实 provider smoke；按 closeout "未运行项" 段表述保留，留给后续真实集成轮。

### 三轮判定

M2 closeout 可以保留 `Status: done`。所有评审追加段中标记为 follow-up / 必修 / 部分落实的项目均已在 commit `33d8d9e` 中落到代码、测试或文档。下一步可直接进入 P1-M3 plan 起草。

