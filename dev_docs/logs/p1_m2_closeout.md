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

