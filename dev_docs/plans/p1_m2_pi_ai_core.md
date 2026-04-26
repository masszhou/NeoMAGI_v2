---
doc_id: 019dcb8e-6ac7-7343-98d4-a5f5be6c1887
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-26T22:50:01+02:00
---
# P1-M2 Implementation Plan: `pi-ai` Python Core

- Status: accepted
- Date: 2026-04-26
- Roadmap: `design_docs/roadmap/p1_engine_pi.md` (§ P1-M2)
- Architecture: `design_docs/architecture/p1_pi_cli_technical_architecture.md`
  § Model and Provider
- Pi-mono baseline: `97a38bf6` (ADR-0011)
- Local reference clone: `/Users/zhiliangzhou/devel/pi-mono`
- Governing decisions:
  - ADR-0009 Pi CLI product equivalence contract
  - ADR-0010 Pydantic v2 protocol types
  - ADR-0011 Freeze pi-mono baseline at `97a38bf6`
  - ADR-0016 Provider-side prompt cache contract
  - ADR-0017 Use provider SDKs for OpenAI and Anthropic
- Reference files:
  - `packages/ai/src/types.ts`
  - `packages/ai/src/providers/anthropic.ts`
  - `packages/ai/src/providers/openai-responses.ts`
  - `packages/ai/src/providers/openai-responses-shared.ts`
  - `packages/ai/src/providers/openai-completions.ts`
  - `packages/ai/src/providers/simple-options.ts`
  - `packages/ai/src/models.ts`
  - `packages/ai/test/cache-retention.test.ts`
  - `packages/ai/test/openai-completions-prompt-cache.test.ts`

## 目标

落实 P1-M2：在 M0 已有协议类型、M1 已有 TUI playback contract 的基础上，交付
Python 版 `pi-ai` 核心，让后续 M3 `agent_core` 可以把 provider stream 直接接入
agent loop，让 M4 TUI 可以消费同一套 `AssistantMessageEvent`。

M2 完成后必须支持两个真实 provider：

- OpenAI：Responses API 为默认 direct OpenAI 路径；Chat Completions 提供 direct
  OpenAI 与 OpenAI-compatible custom model 的最小兼容路径。
- Anthropic：Messages API direct API key 路径。

M2 还必须交付 faux provider、provider/model registry、tool argument validation、
usage/cost 五维归一化、prompt cache contract、opaque continuation 字段透传和
cross-provider handoff fixture。

## 用户明确需求

本计划把以下要求作为 M2 core，不降级为 stretch：

- 同时支持 OpenAI 和 Anthropic 两个 provider；用户两边都是重度使用者，不能只交付
  一个真实 provider。
- 承认两者请求/stream/usage 格式不同，provider adapter 分开实现，不用一个过宽的
  "generic LLM" 请求结构硬套。
- OpenAI prompt cache 不做本地缓存，也不手动给 prompt block 打
  `cache_control`。M2 只传 provider 支持的请求级 cache affinity，并把 provider
  返回的 cached token usage 归一化到 `Usage.cacheRead/cacheWrite`。
- Anthropic prompt cache 也不在 NeoMAGI 本地存缓存。M2 只参考 pi-mono 的简易
  Anthropic 支持：给 Anthropic Messages API 的 prompt 片段打
  `cache_control` 标记，cache hit/write 由 Anthropic 返回的 usage 体现。
- 不复刻 Claude Code 复杂 prompt cache 机制。Claude Code OAuth、stealth tool
  name mapping、完整 subscription flow 留给 M9 或后续 ADR。

## 范围

### In scope

- `ai_provider` runtime API：
  - `AssistantMessageEventStream` async iterable，支持 `result()` / `close()` /
    abort propagation。
  - `StreamOptions` / `SimpleStreamOptions` / `ProviderResponse` / stream function
    protocol。
  - `stream(model, context, options)` 和 `stream_simple(model, context, options)`。
- Provider registry：
  - 按 API family 注册：`anthropic-messages`、`openai-responses`、
    `openai-completions`、`faux`。
  - 支持 extension/M9 后续注册自定义 provider 的内存接口，但 M2 不做持久化。
- Model registry：
  - 内建最小 model 集：至少一个 Anthropic Messages model、一个 OpenAI
    Responses model、一个 OpenAI Chat Completions model、一个 faux model。
  - `contextWindow` 必填，cost 保留 `input/output/cacheRead/cacheWrite` 五维。
  - model id、cost、context window 从 pinned pi-mono baseline 或显式 fixture
    复制，不在代码里臆造。
- Provider adapters：
  - Anthropic Messages：text、image、thinking、tool call、tool result、streaming
    usage、cache_control。
  - OpenAI Responses：text streaming、function/tool call、usage cached token、
    request-level prompt cache affinity。
  - OpenAI Chat Completions：text streaming、tool call、usage cached token、
    direct OpenAI prompt cache fields、OpenAI-compatible custom model substrate。
  - Faux provider：text、thinking、tool call、error、abort、prompt cache simulation。
- Prompt cache contract：
  - `cacheRetention = none | short | long`，默认 `short`。
  - `PI_CACHE_RETENTION=long` 可把默认提升到 `long`。
  - `cacheRetention="none"` 强禁用所有 cache/session propagation。
  - `sessionId` 在 M2 中只代表 provider cache affinity id；durable Postgres
    session id 到 affinity id 的生成/继承策略留 M3/M6 落地。
- Usage/cost：
  - `Usage.input` 排除 `cacheRead/cacheWrite`。
  - `totalTokens = input + output + cacheRead + cacheWrite`。
  - `calculate_cost()` 使用 model cost 的五维价格计算。
  - 每个真实 provider family 至少一条 raw usage -> normalized usage fixture。
- Tool schema / arguments：
  - 使用 JSON Schema validator 校验 tool call arguments。
  - provider 只负责把 tool call event 解析成 Pi-compatible `ToolCall`；实际工具
    执行在 M3/M5。
- Opaque continuation：
  - `thinkingSignature`、`thoughtSignature`、`textSignature`、`responseId`、
    provider-specific extra fields 在 parse/serialize/convert 过程中不丢。
- Credential 基础：
  - 仅实现 runtime `apiKey` override + 环境变量读取。
  - 不写 auth storage，不落 secret，不做 OAuth。

### Out of scope

- `agent_core.Agent` loop、tool execution 回灌、steer/follow-up queue：M3。
- TUI 接真实 runtime：M4。
- coding tools、bash/download/edit/write policy：M5。
- Postgres session 持久化、JSONL import/export：M6/M10。
- settings/auth/model selector 持久化、OAuth provider：M9。
- Bedrock `cachePoint`、OpenRouter 完整 routing、Copilot、Gemini、Groq、xAI、
  Ollama 等完整 provider matrix。
- Claude Code stealth tool-name mapping 和 Anthropic OAuth identity prompt。
- 本地 prompt cache 存储、缓存驱逐、cache key 数据库。

## 设计约束

### Provider 分层

M2 不建立一个会泄漏 provider 细节的通用 payload 层。统一边界只到
`Context`、`Message`、`Tool`、`StreamOptions`、`AssistantMessageEvent`。
每个 provider adapter 独立负责：

- message/tool conversion；
- request params；
- SDK/raw stream event parsing；
- usage extraction；
- provider-specific cache handling；
- provider error -> Pi-compatible `StreamError`。

这和 pi-mono 的 `packages/ai/src/providers/*.ts` 边界保持一致。

### Provider SDK Runtime

ADR-0017 已决定：M2 的 OpenAI 和 Anthropic provider runtime 使用官方 Python
SDK，不自建共享 HTTP/SSE substrate。

计划新增生产依赖：

- `openai`：OpenAI Responses / Chat Completions provider transport。
- `anthropic`：Anthropic Messages provider transport。
- `jsonschema>=4,<5`：按工具提供的 JSON Schema 校验 tool arguments。

SDK 只负责 transport / request runtime。NeoMAGI 仍保留自己的 provider adapter：
message/tool conversion、provider params、stream event parsing、usage extraction、
error normalization 和 `AssistantMessageEventStream` 映射都在本仓库完成。SDK stream
chunk 或 raw response event 不穿透到 agent/TUI/session protocol。

Anthropic adapter 如果需要像 pi-mono 一样读取 SDK raw response body 并自行做 SSE
decode / JSON repair，该逻辑只能留在 Anthropic provider 内部，不能抽成 M2 的共享
transport 前提。未来如果要移除 SDK、统一 HTTP/SSE substrate，必须新增 ADR。

### Prompt cache 不是 NeoMAGI truth

Prompt cache 是 provider-side optimization。NeoMAGI 在 M2 只保存/暴露：

- request options 中的 `cacheRetention`；
- 传给 provider 的 cache affinity id；
- request payload/header metadata；
- provider 返回的 `Usage.cacheRead/cacheWrite` 和 cost。

NeoMAGI 不保存 provider cache contents，不把 cache hit 当作 durable state，也不把
cache miss 当作错误。

## Prompt Cache Contract

### 共同规则

`src/ai_provider/prompt_cache.py` 提供共享 helper：

- `resolve_cache_retention(cache_retention: CacheRetention | None) -> CacheRetention`
  - explicit option 优先；
  - `PI_CACHE_RETENTION=long` 时默认 `long`；
  - 否则默认 `short`。
  - M2 只 honor `PI_CACHE_RETENTION` 这一条 cache env；NeoMAGI 命名的 settings/env
    留到 M9，不在 W2 额外发明 `NEOMAGI_CACHE_RETENTION`。
- `cache_enabled(retention) -> bool`
  - `none` 返回 false。
- `sanitize_cache_affinity_id(session_id: str | None) -> str | None`
  - M2 默认只透传合法字符串；
  - durable id 映射策略不在 M2 生成。

强规则：`cacheRetention == "none"` 时，任何 provider adapter 都不能发送：

- `sessionId`
- `prompt_cache_key`
- `prompt_cache_retention`
- `cache_control`
- `cachePoint`
- `session_id`
- `x-client-request-id`
- `x-session-affinity`

### Anthropic Messages

参考 pi-mono `packages/ai/src/providers/anthropic.ts`：

- `get_cache_control(base_url, retention)`：
  - `none` 返回无 cache control；
  - `short` 返回 `{ "type": "ephemeral" }`；
  - direct `api.anthropic.com` + `long` 返回
    `{ "type": "ephemeral", "ttl": "1h" }`；
  - proxy + `long` 不加 `ttl`。
- `build_params()`：
  - system prompt block 加 `cache_control`；
  - M2 direct API-key path 不注入 Claude Code identity system prompt；
  - tools 存在时只给最后一个 tool definition 加 `cache_control`；
  - messages 转换后，只给最后一个 `user` message 的最后一个可标记 block 加
    `cache_control`；
  - 如果最后一个 user content 是 string，转成 text block array 后再加标记。
- usage：
  - `cache_read_input_tokens` -> `Usage.cacheRead`；
  - `cache_creation_input_tokens` -> `Usage.cacheWrite`；
  - `totalTokens` 由 input/output/cacheRead/cacheWrite 计算；
  - `calculate_cost()` 使用 Anthropic model cost 的 cache read/write 价格。

M2 不实现比上述更复杂的 prompt cache。特别是，不维护本地 cache map，不生成本地
prompt prefix key，不模拟 Claude Code 多段 cache strategy。

### OpenAI Responses

参考 pi-mono `packages/ai/src/providers/openai-responses.ts` 与
`openai-responses-shared.ts`：

- cache enabled 且有 `sessionId` 时，Responses payload 发送
  `prompt_cache_key = sessionId`。
- direct `api.openai.com` + `cacheRetention="long"` 时发送
  `prompt_cache_retention = "24h"`。
- `cacheRetention="none"` 时不发送 `prompt_cache_key`、`prompt_cache_retention`
  或 affinity headers。
- cache enabled 且有 `sessionId` 时，无条件发送 `session_id` 与
  `x-client-request-id` header；options headers 必须最后 merge，允许用户覆盖。
- usage：
  - `response.usage.input_tokens_details.cached_tokens` -> `Usage.cacheRead`；
  - OpenAI Responses 没有 cache write token，`Usage.cacheWrite = 0`；
  - `Usage.input = input_tokens - cached_tokens`。

OpenAI Responses 不给 system/user/tool block 打 `cache_control`。

### OpenAI Chat Completions

参考 pi-mono `packages/ai/src/providers/openai-completions.ts`：

- direct `api.openai.com` + cache enabled + `sessionId` 时发送
  `prompt_cache_key = sessionId`。
- direct `api.openai.com` + `cacheRetention="long"` 时发送
  `prompt_cache_retention = "24h"`。
- 非 direct OpenAI base URL 默认不发送 prompt cache fields。
- OpenAI-compatible provider 只有在 `model.compat.sendSessionAffinityHeaders == true`
  时发送 `session_id`、`x-client-request-id`、`x-session-affinity`。
- usage：
  - `prompt_tokens_details.cached_tokens` -> reported cached tokens；
  - `prompt_tokens_details.cache_write_tokens` -> cache write；
  - 如果 provider 把 read + write 都算进 cached tokens，则
    `cacheRead = max(cached_tokens - cache_write_tokens, 0)`；
  - `input = prompt_tokens - cacheRead - cacheWrite`。

Direct OpenAI Chat Completions 不给 prompt block 打 `cache_control`。
OpenAI-compatible provider 的 Anthropic-style `cache_control` opt-in 是后续独立兼容
行为，不进入 M2 core。后续若要支持，必须单独记录 provider compatibility 行为；
它不能改变 direct OpenAI provider contract，也不能成为 OpenAI cache 的默认语义。

## 工作分解

| ID | 工作项 | 产出 |
| --- | --- | --- |
| W0 | M2 runtime API 与依赖 | `pyproject.toml` 增 OpenAI / Anthropic SDK + `jsonschema`；`src/ai_provider/streaming.py`；`src/ai_provider/runtime_types.py` |
| W1 | Provider/model registry | `src/ai_provider/api_registry.py`、`src/ai_provider/models.py`、`src/ai_provider/model_registry.py` |
| W2 | Shared provider utilities | `src/ai_provider/prompt_cache.py`、`src/ai_provider/credentials.py`、`src/ai_provider/tools.py` |
| W3 | Usage/cost normalization | 完成 `src/ai_provider/usage.py` provider-specific extractors + fixtures |
| W4 | Faux provider | `src/ai_provider/providers/faux.py` + deterministic stream fixtures |
| W5 | Anthropic Messages provider | `src/ai_provider/providers/anthropic.py` + cache/usage/tool/thinking tests |
| W6 | OpenAI Responses provider | `src/ai_provider/providers/openai_responses.py` + streaming/cache/usage tests |
| W7 | OpenAI Chat Completions provider | `src/ai_provider/providers/openai_completions.py` + direct cache/custom compatible tests |
| W8 | Cross-provider conversion / opaque fields | `src/ai_provider/convert.py` + handoff regression fixtures |
| W9 | Test suite and fixture expansion | `tests/ai_provider/test_*.py` + `tests/fixtures/pi_compat/*` |
| W10 | Closeout docs | `dev_docs/progress/progress.md` append + `dev_docs/logs/p1_m2_closeout.md` |

### W0. Runtime API 与依赖

新增 `src/ai_provider/runtime_types.py`：

- `ProviderResponse(status: int, headers: dict[str, str])`
- `StreamOptions` / `SimpleStreamOptions` 使用 dataclass，不使用 pydantic，避免和
  ADR-0010 的 wire-protocol model 混淆。
- `StreamOptions`
  - `temperature`
  - `max_tokens`
  - `signal: asyncio.Event | None` 作为 M2 cancellation token
  - `api_key`
  - `transport`
  - `cache_retention`
  - `session_id`
  - `on_payload`
  - `on_response`
  - `headers`
  - `max_retry_delay_ms`
  - provider metadata passthrough
- `SimpleStreamOptions`
  - 继承/组合 base options；
  - `reasoning`
  - `thinking_budgets`
- `StreamFunction` protocol
- `ProviderAdapter` protocol

SDK client injection contract：

- M2 不 monkey-patch 全局 SDK class 作为默认测试策略。
- 每个 SDK-backed provider option dataclass 暴露同名 `client: object | None = None`
  注入位；provider module 内部把它 cast 到 adapter-local client protocol。
- fake client 只实现 adapter 实际使用的最小 SDK surface：
  - Anthropic Messages：messages create / stream call、response metadata、async event
    iterator 或 raw response body（取决于 W5 选定 SDK 调用形态）。
  - OpenAI Responses：responses create stream、response metadata、typed stream events。
  - OpenAI Chat Completions：chat.completions create stream、response metadata、chunk
    usage。
- 这三个 fake contract 必须在对应 provider test 文件顶部用小型 fake class 固定，
  不允许 W5/W6/W7 各自用不同 monkeypatch 风格。

依赖更新：

- 增加 OpenAI Python SDK。
- 增加 Anthropic Python SDK。
- 增加 `jsonschema>=4,<5`。
- 不把 `httpx` 作为 M2 OpenAI/Anthropic provider transport 的默认依赖；如果 SDK
  自身依赖或其他基础设施间接使用 `httpx`，不改变 ADR-0017 的 ownership。
- W0 实施时必须在 `pyproject.toml` 写明 OpenAI / Anthropic SDK 的版本下限，并在
  closeout 记录选择依据；plan 不在此硬编码未来可能漂移的 SDK 版本号。

新增 `src/ai_provider/streaming.py`：

- `AssistantMessageEventStream`：
  - async iterable；
  - `push(event)`；
  - `end()`；
  - `error(message: str)`：caller 只传错误文本，stream 内部基于当前 partial
    assistant snapshot 合成 `StreamError(reason="error", error=AssistantMessage)`；
    abort 路径使用 `reason="aborted"`；
  - `result()` 返回 final `AssistantMessage`；
  - `close()` 触发 abort/cancel；
  - `partial` 语义保持 Pi contract：delta frame 的 `partial` 是当前完整快照。
- `create_assistant_message_event_stream()` helper，作为 architecture 指定的
  pi-ai module-level helper。

验收：

- faux provider 可用该 stream 产生 12 帧 `AssistantMessageEvent`。
- `result()` 与 final `done.message` / `error.error` 是同一份 final snapshot。
- abort 后 stream 产生 `StreamError(reason="aborted")`，并清理 provider scratch
  字段，如 `partialJson`。

### W1. Provider/model registry

新增 `src/ai_provider/api_registry.py`：

- `register_api(api_name, stream_fn, stream_simple_fn=None)`
- `unregister_api(api_name)`
- `get_api(api_name)`
- `stream(model, context, options=None)`
- `stream_simple(model, context, options=None)`

新增 `src/ai_provider/model_registry.py`：

- `register_model(model: Model)`
- `get_model(provider: str, model_id: str) -> Model`
- `list_models(provider: str | None = None) -> list[Model]`
- `resolve_model(model_ref: str)`，M2 只支持显式 provider/model 形式。

新增/完善 `src/ai_provider/models.py`：

- 最小 built-in model set；
- `calculate_cost()` 从 `usage.py` re-export 或迁移到该文件，保持 pi-mono
  `packages/ai/src/models.ts` 语义；
- `supports_xhigh()`、`supports_reasoning()` 等只实现 M2 provider 需要的最小子集。
- Model register-time validation：
  - `context_window <= 0` 抛 `ValueError`；
  - `max_tokens <= 0` 抛 `ValueError`；
  - `model.compat` 不继续裸用 `dict[str, Any]` 做运行时 contract。M2 至少定义
    typed `OpenAICompletionsCompat` schema，覆盖 direct prompt cache、session
    affinity headers 和 custom compatible model 需要的字段；其他 provider compat
    留 `extra` 透传但不得驱动分支逻辑。

验收：

- registry 按 API family 分发，而不是按 provider vendor 分发。
- OpenAI provider 可同时注册 Responses 和 Chat Completions 两个 API family。
- model `contextWindow` 缺失或 `contextWindow/maxTokens <= 0` 时 validation fail。

### W2. Shared provider utilities

新增 `src/ai_provider/prompt_cache.py`：

- 实现本计划的共同 cache helper。
- 所有 provider cache tests 都只通过该 helper 判断 retention，不复制分支。

新增 `src/ai_provider/credentials.py`：

- `get_env_api_key(provider)`：
  - OpenAI: `OPENAI_API_KEY`
  - Anthropic: `ANTHROPIC_API_KEY`
  - custom provider: 后续 M9 扩展
- `resolve_api_key(model, options)`：
  - `options.api_key` 优先；
  - env fallback；
  - 缺失时抛明确错误，不读取或写入任何 secret 文件。

新增 `src/ai_provider/tools.py`：

- `validate_tool_arguments(tool: Tool, arguments: dict) -> None`
- 使用 `jsonschema` Draft 2020-12；
- 校验失败产生可序列化错误。
- M2 不在任何 provider stream 路径上调用该校验；M3 agent loop 在 dispatch tool
  之前调用，校验失败合成 `ToolResultMessage(isError=True)`。

Provider-specific parser 放置规则：

- OpenAI provider 优先消费 OpenAI SDK typed stream event，再映射到
  `AssistantMessageEventStream`。
- Anthropic provider 优先消费 Anthropic SDK stream/raw response；如果需要 SSE
  decode / JSON repair，相关 helper 放在 `providers/anthropic.py` 或
  `providers/anthropic_*.py` 内，不放入共享 `ai_provider.sse` substrate。

### W3. Usage/cost normalization

完善 `src/ai_provider/usage.py`：

- `normalize_anthropic_usage(raw, model) -> Usage`
- `normalize_openai_responses_usage(raw, model) -> Usage`
- `normalize_openai_completions_usage(raw, model) -> Usage`
- `normalize_faux_usage(raw, model) -> Usage`
- M0 placeholder `normalize_provider_usage(raw, provider)` 在 W3 完成后删除，由上述
  typed extractor 替代，避免两套 normalization 路径并存。

规则：

- `Usage.input` 不含 cache read/write。
- `Usage.cacheRead` 和 `Usage.cacheWrite` 不互相覆盖。
- OpenAI-compatible provider 的 `cached_tokens` 若同时含 read/write，必须减掉
  write 后才算 read。
- `Usage.totalTokens` 由 NeoMAGI 自己计算，除非 provider total 与计算值一致；
  fixture 以计算值为准。
- `calculate_cost(model, usage)` 必须覆盖 cache read/write cost。

Fixture：

- `tests/fixtures/pi_compat/usage_cache_normalization/anthropic.json`
- `tests/fixtures/pi_compat/usage_cache_normalization/openai_responses.json`
- `tests/fixtures/pi_compat/usage_cache_normalization/openai_completions.json`
- `tests/fixtures/pi_compat/usage_cache_normalization/openai_compatible_cache_write.json`

### W4. Faux provider

新增 `src/ai_provider/providers/faux.py`：

- 支持 scripted response：
  - text delta；
  - thinking delta；
  - tool call argument streaming；
  - error；
  - abort；
  - empty response。
- 支持 deterministic usage：
  - 简单 token 估算，不追求 tokenizer 等价；
  - 相同 `sessionId` + common prefix 模拟 `cacheRead`；
  - `cacheRetention="none"` 时不产生 cache read/write。
- 支持 `on_payload` / `on_response`，方便 M3/M4 测试。

验收：

- 不访问网络。
- 能复用 M1 playback fixtures 中的 text/thinking/tool/error/abort event contract。
- prompt cache fixtures 不依赖真实 provider key。

### W5. Anthropic Messages provider

新增 `src/ai_provider/providers/anthropic.py`。

Request conversion：

- `systemPrompt` -> Anthropic `system` text block；
- `UserMessage.content` string -> text block when cache marker is needed；
- image block -> Anthropic base64 image source；
- assistant text/thinking/toolCall -> Anthropic assistant content blocks；
- redacted thinking / thinking signature 作为 opaque continuation 透传；
- toolResult messages 合并为 user message 的 `tool_result` blocks；
- tool schema -> Anthropic `input_schema`；
- 只给最后一个 tool definition 加 `cache_control`。

Stream parsing：

- `message_start` -> set `responseId` and initial usage；
  - 必须立即 capture `input/output/cacheRead/cacheWrite`，因为 abort fixture 仍需要
    input token 计数；
- `content_block_start` / `content_block_delta` / `content_block_stop`：
  - text -> `text_start` / `text_delta` / `text_end`；
  - thinking -> `thinking_start` / `thinking_delta` / `thinking_end`；
  - tool_use -> `toolcall_start` / `toolcall_delta` / `toolcall_end`；
- `message_delta` -> stop reason + usage update；
- `message_stop` -> final `done`。

Prompt cache：

- 按上文 Anthropic Messages 规则实现。
- tests 覆盖 default short、explicit long、env long、none、proxy long no ttl。

验收：

- 使用 fake Anthropic SDK client / raw response 可捕获 outgoing params 并断言
  system/user/tool 三处 cache marker。
- real provider smoke 在 `ANTHROPIC_API_KEY` 存在且显式打开时运行，不作为默认
  CI 前置。

### W6. OpenAI Responses provider

新增 `src/ai_provider/providers/openai_responses.py`。

Request conversion：

- `Context.messages` -> Responses `input`；
- tool definitions -> Responses tools；
- system prompt 作为 instructions/developer input 的具体映射必须有 fixture；
- `store=false`；
- `stream=true`；
- direct OpenAI long retention -> `prompt_cache_retention="24h"`。

Stream parsing：

- text delta -> Pi text frames；
- `response.refusal.delta` 与 `response.output_text.delta` 同等处理，写入当前 text
  block；如果业务侧需要区分 refusal，由 M3/M4 在 agent/UI 层基于 message metadata
  或 error marker 展示，不丢拒答文本。
- function call argument delta -> Pi toolCall frames；
- `response.completed` -> response id、usage、stop reason；
  - 如果 output content 含 toolCall 且当前 stop reason 是 `stop`，重写为
    `toolUse`；
  - `textSignature` 使用 JSON string 编码并提供 decode helper，最小结构为
    `{ "v": 1, "id": output_message_item_id, "phase": optional_phase }`；
    不要把 top-level `responseId` 灌进 `textSignature.id`，二者都是独立的 opaque
    continuation 字段；
- provider error/cancel -> `StreamError`。

Prompt cache：

- `sessionId` -> `prompt_cache_key` when cache enabled；
- cache enabled + `sessionId` -> `session_id` and `x-client-request-id` headers；
- no block-level `cache_control`；
- `cacheRetention="none"` removes all cache/session fields。

验收：

- Fake OpenAI SDK client captures params for short/long/none prompt cache fixtures。
- usage fixture covers `input_tokens_details.cached_tokens` and `cacheWrite=0`。
- real smoke with `OPENAI_API_KEY` can complete one streaming text reply.

### W7. OpenAI Chat Completions provider

新增 `src/ai_provider/providers/openai_completions.py`。

Purpose：

- Direct OpenAI fallback for models that still use Chat Completions。
- OpenAI-compatible custom model substrate for M9。
- Anthropic-style `cache_control` compatible-provider path 明确 deferred；M2 不把
  `cacheControlFormat="anthropic"` 纳入 core implementation。

Request conversion：

- system/developer role compatibility handled by `compat.supportsDeveloperRole`；
- text/image content parts；
- assistant tool calls；
- tool result messages；
- `stream_options.include_usage = true` when provider supports it；
- strict mode/tool schema minimal support。

Prompt cache：

- direct OpenAI only: `prompt_cache_key` + optional `prompt_cache_retention="24h"`。
- proxy/default compatible: no prompt cache fields。
- `compat.sendSessionAffinityHeaders == true` sends the three affinity headers。
- `cacheControlFormat="anthropic"` 不在 M2 实现；保留为 M9/backlog 兼容项。

Usage：

- parse final chunk usage；
- normalize `cached_tokens` and `cache_write_tokens` per pi-mono rule。
- chunk 里出现 `reasoning_details[].type == "reasoning.encrypted"` 时，按 id 匹配
  到 `toolCall.thoughtSignature = json.dumps(detail)`。
- assistant streaming 从 `reasoning_content` / `reasoning` / `reasoning_text` 三个字段
  中取第一个非空字段：thinking 内容来自该字段的累积 delta；`thinkingSignature`
  存该字段名本身，用于跨轮重建 outgoing payload 时知道写回哪个字段。

验收：

- `openai-completions-prompt-cache` fixture parity with pi-mono tests。
- Direct OpenAI Chat Completions 不产生任何 block-level `cache_control`。
- Closeout 明确记录 `cacheControlFormat="anthropic"` deferred 到 M9/backlog。

### W8. Cross-provider conversion / opaque fields

新增 `src/ai_provider/convert.py`：

- shared helpers for provider message conversion；
- no mutation of original `Context` / `Message` objects；
- unsupported provider-specific blocks degrade explicitly at outgoing payload boundary；
- opaque fields remain in pydantic extras during round-trip。

Negative fixture：

- Anthropic response with `thinkingSignature` / `thoughtSignature` / `responseId`
  is serialized, deserialized, sent through OpenAI-compatible conversion, then sent
  back through Anthropic conversion without losing opaque fields from the stored
  message.

验收：

- cross-provider handoff 后下一轮不会因 missing continuation field 或 mutated content
  失败。
- provider converters only shape outgoing payload; they do not rewrite durable message
  records.

### W9. Tests and fixture expansion

新增测试目录：

- `tests/ai_provider/test_streaming.py`
- `tests/ai_provider/test_registry.py`
- `tests/ai_provider/test_prompt_cache.py`
- `tests/ai_provider/test_usage.py`
- `tests/ai_provider/test_faux_provider.py`
- `tests/ai_provider/test_anthropic_provider.py`
- `tests/ai_provider/test_openai_responses_provider.py`
- `tests/ai_provider/test_openai_completions_provider.py`
- `tests/ai_provider/test_tool_arguments.py`
- `tests/ai_provider/test_cross_provider_handoff.py`
- `tests/ai_provider/test_import_boundaries.py`

新增/补全 fixtures：

- `tests/fixtures/pi_compat/cache_retention_none/`：现有 fixture 升级为 provider
  matrix。
- `tests/fixtures/pi_compat/anthropic_cache_short/`
- `tests/fixtures/pi_compat/anthropic_cache_long/`
- `tests/fixtures/pi_compat/anthropic_cache_none/`
- `tests/fixtures/pi_compat/openai_responses_prompt_cache/`
- `tests/fixtures/pi_compat/openai_completions_prompt_cache/`
- `tests/fixtures/pi_compat/usage_cache_normalization/`
- `tests/fixtures/pi_compat/provider_stream_text/`
- `tests/fixtures/pi_compat/provider_stream_tool_call/`
- `tests/fixtures/pi_compat/provider_abort/`
- `tests/fixtures/pi_compat/cross_provider_handoff_opaque/`
- `tests/fixtures/pi_compat/tool_argument_validation/`

Test quality rules：

- 默认测试全部离线，使用 fake SDK clients / fake SDK stream events / faux provider。
- 真实 provider smoke 必须显式 opt-in，例如 `NEOMAGI_PROVIDER_SMOKE=1`，并在 key
  缺失时 skip。
- 不接受只检查文件存在的 weak test。
- Cache tests 必须检查 outgoing payload/header 的 forbidden keys，尤其是
  `cacheRetention="none"`。
- Usage tests 必须断 `input/cacheRead/cacheWrite/totalTokens/cost` 全字段。
- Stream tests 必须断 event 顺序和 final snapshot，不只断最终文本。
- Import-boundary static scan：
  - `src/ai_provider/providers/{anthropic,openai_responses,openai_completions}.py`
    允许 import `anthropic` / `openai`；
  - `src/ai_provider/**` 其他文件一律不允许 import `anthropic` / `openai`，包括
    provider-neutral helper such as `overflow.py`；
  - `src/agent_core/**` 和 `src/cli/**` 不允许 import `anthropic` / `openai`。

### W10. Closeout docs

M2 完成时新增：

- `dev_docs/logs/p1_m2_closeout.md`
- `dev_docs/progress/progress.md` 一条 closeout entry

Closeout 必须记录：

- OpenAI/Anthropic provider 支持矩阵；
- prompt cache payload/header 证据；
- usage normalization fixture 证据；
- real provider smoke 是否运行，若未运行说明原因；
- M3 接入需要的 `stream()` contract；
- 未做的 provider/OAuth/cache stretch。

## 验收标准

M2 accepted 需要同时满足：

- `stream(faux, context)` 能跑通 text、thinking、tool call、error、abort。
- `stream(anthropic, context)` 在 fake Anthropic SDK client / stream 下能完成
  streaming text 和 tool call；real smoke 可选但路径存在。
- `stream(openai-responses, context)` 在 fake OpenAI SDK client / stream 下能完成
  streaming text 和 tool call；real smoke 可选但路径存在。
- `stream(openai-completions, context)` 覆盖 direct OpenAI prompt cache 和
  OpenAI-compatible custom model 的非 direct OpenAI 基础路径。
- M2 产生的 `AssistantMessageEvent` 能被 M1 `EventRouter` 识别，不新增 UI-only
  协议。
- Prompt cache fixtures 覆盖：
  - disabled；
  - short；
  - long；
  - Anthropic cache_control marker；
  - OpenAI prompt_cache_key / prompt_cache_retention；
  - cache read/write usage normalization。
- 序列化/反序列化/fixture round-trip 不丢 `thinkingSignature`、
  `thoughtSignature`、`textSignature`、`responseId`。
- Tool argument validation 能对非法参数产生失败证据。
- `uv run pytest tests/` green。
- `just lint` green。
- `complexity_guard` 0 regression，新增复杂 provider parser 如超阈值，必须先拆
  helper，不用 baseline 掩盖。

## 实施顺序

1. W0/W1/W2：先建立 runtime API、registry、SDK client injection、cache/tool
   helper。
2. W3/W4：补 usage normalization 和 faux provider，让无网测试先闭环。
3. W5：实现 Anthropic Messages。Anthropic cache_control 是本计划的最高风险差异，
   先用 payload fixture 锁住。
4. W6：实现 OpenAI Responses direct provider。
5. W7：实现 OpenAI Chat Completions 和 OpenAI-compatible custom model substrate；
   `cacheControlFormat="anthropic"` deferred。
6. W8/W9：补 cross-provider handoff、opaque fields、完整 fixture。
7. W10：closeout，并明确 M3 如何调用 `stream()`。

## 风险与处理

- Provider stream event 形状漂移：M2 默认测试使用 fake SDK stream/raw event
  fixtures；真实 smoke 只作为补充证据。
- Prompt cache semantics 易混：Anthropic 是 block marker，OpenAI 是请求级
  affinity/retention。代码用独立 helper 和独立测试锁住，不抽成一个虚假的统一
  payload 字段。
- Usage double counting：OpenAI-compatible providers 可能把 cache write 也放进
  `cached_tokens`。必须用 fixture 锁 `cacheRead = cached - cacheWrite`。
- SDK event 泄漏：官方 SDK 只在 provider adapter 内部使用，SDK chunk/raw event
  不能穿透到 agent/TUI/session protocol。统一边界仍是
  `AssistantMessageEventStream`。
- 自建 transport 回潮：M2 不以共享 `httpx`/SSE substrate 作为 OpenAI/Anthropic
  transport。未来若要移除 SDK，必须新增 ADR。
- Model price/context 变化：M2 只从 pinned baseline 或显式 fixture 复制，后续更新走
  model registry 变更，不在 provider parser 中硬编码价格。
- Opaque field 丢失：所有 provider converter 禁止 mutate 原消息；pydantic extra
  fields 保留由 round-trip fixture 证明。
