---
doc_id: 019dcb9d-855b-768c-8bd4-02ad0f2b81b7
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-26T23:06:31+02:00
---
# 0017-use-provider-sdks-for-openai-and-anthropic

- Status: accepted
- Date: 2026-04-26
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Related: `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md`
- Related: `design_docs/decisions/0016-provider-side-prompt-cache-contract.md`
- Roadmap: `design_docs/roadmap/p1_engine_pi.md` § P1-M2

## 选了什么

- NeoMAGI M2 的 OpenAI 和 Anthropic provider runtime 采用官方 SDK 路线，而不是自建共享 HTTP/SSE substrate。
- Python 实现使用对应 provider 的官方 SDK 作为 transport / request runtime：
  - OpenAI provider 使用 OpenAI Python SDK；
  - Anthropic provider 使用 Anthropic Python SDK。
- NeoMAGI 仍保留自己的 provider adapter 和统一事件模型：
  - adapter 负责 message/tool conversion、provider params、stream event parsing、usage extraction、error normalization；
  - app/agent 层只消费 NeoMAGI 的 `AssistantMessageEventStream` / `AssistantMessageEvent`；
  - SDK stream chunk 或 raw response event 不穿透到 agent protocol。
- 本决策参照 pi-mono 的实际路线。pi-mono `packages/ai/package.json` 将 `@anthropic-ai/sdk` 和 `openai` 作为 runtime dependencies：
  - `@anthropic-ai/sdk: ^0.90.0`；
  - `openai: 6.26.0`。
- pi-mono Anthropic 路径：
  - `packages/ai/src/providers/anthropic.ts` 从 `@anthropic-ai/sdk` import `Anthropic`；
  - `createClient()` 中 `new Anthropic(...)`；
  - 请求使用 `client.messages.create(...).asResponse()`；
  - SSE decode / JSON repair 仍由 pi-mono 自己实现：`iterateSseMessages()` / `iterateAnthropicEvents()` 从 raw `Response.body` 读取事件。
- pi-mono OpenAI Responses 路径：
  - `packages/ai/src/providers/openai-responses.ts` import `OpenAI`；
  - `new OpenAI(...)`；
  - 请求使用 `client.responses.create(...).withResponse()`；
  - stream event 再交给 `packages/ai/src/providers/openai-responses-shared.ts` 转成 pi 自己的 event stream。
- pi-mono OpenAI Chat Completions 路径：
  - `packages/ai/src/providers/openai-completions.ts` import `OpenAI`；
  - 请求使用 `client.chat.completions.create(...).withResponse()`；
  - provider adapter 自己把 SDK stream chunk 映射到统一事件。
- pi-mono 的统一层是 `packages/ai/src/utils/event-stream.ts`：它是 app/provider event abstraction，不是 HTTP/SSE substrate。

## 为什么

- M2 的目标是交付 OpenAI 和 Anthropic 两个真实 provider 的最小可用闭环，而不是先实现和验证一套通用 HTTP/SSE runtime。
- OpenAI 与 Anthropic 的请求体、stream event、usage、thinking/tool continuation、cache 字段和错误形态差异很大。共享 substrate 容易把复杂度提前堆到低层，反而削弱 provider adapter 的清晰边界。
- 官方 SDK 负责认证、base URL、headers、request construction、stream transport、abort integration 和 provider API 细节演进，降低 M2 的协议追赶成本。
- pi-mono 已证明这条路线可行：SDK 负责 transport，项目自身负责统一事件模型和 provider-specific normalization。
- NeoMAGI 的长期可控点应放在稳定 protocol、usage normalization、tool contract、memory/session truth 和 policy boundary，而不是重复维护 provider HTTP client。
- 对 M2 来说，减少自建 transport 能降低测试面：主要 mock SDK client / stream event，而不是同时 mock HTTP chunking、SSE framing、retry、timeout 和 provider event schema。

## 放弃了什么

- 方案 A：共享 `httpx` HTTP/SSE substrate 直连 OpenAI 和 Anthropic。
  - 放弃原因：实现面会覆盖 transport、SSE parser、retry、timeout、headers、provider-specific request schema 和 stream schema；M2 风险高于收益。
- 方案 B：完全复刻 pi-mono 的 Node SDK 细节。
  - 放弃原因：NeoMAGI 是 Python runtime，应采用 Python 官方 SDK 的自然接口；只复用 pi-mono 的分层思想和 provider contract，不追逐逐行实现。
- 方案 C：让 SDK stream event 直接成为 NeoMAGI agent protocol。
  - 放弃原因：会把 provider lock-in 泄漏到 agent/TUI/session 层，破坏 cross-provider handoff 和后续 provider 扩展。
- 方案 D：为所有 provider 强制同一个 generic request payload。
  - 放弃原因：OpenAI Responses、OpenAI Chat Completions 和 Anthropic Messages 的语义不同；统一边界应停在 NeoMAGI `Context` / `Message` / `Tool` / `StreamOptions` / `AssistantMessageEvent`。

## 影响

- M2 可以新增 OpenAI 和 Anthropic 官方 Python SDK 作为生产依赖；不再以共享 HTTP/SSE substrate 作为 M2 provider transport 前提。
- `httpx` 只能作为其他基础设施或 SDK 外围需要时的普通依赖评估，不能作为 M2 OpenAI/Anthropic provider 的自建 transport 决策默认值。
- Provider adapter 仍必须有独立测试：SDK client / stream event 可 mock，但不能调用真实 provider API、真实 API key 或付费 token。
- Anthropic adapter 可以在 SDK 返回 raw response 时自行处理 SSE decode / JSON repair；这属于 provider adapter 内部实现，不构成共享 substrate。
- OpenAI adapter 可以复用 SDK typed stream event，再转成 NeoMAGI `AssistantMessageEventStream`。
- `AssistantMessageEventStream` 是统一事件抽象，不拥有 HTTP/SSE lifecycle。它只表示 provider adapter 已经解析后的 NeoMAGI event 序列。
- 特殊 provider 如果 SDK 不覆盖，例如未来 Codex-like endpoint，可以单独使用 `fetch`/HTTP client + local SSE parser；这属于 provider-specific exception，不能反推 M2 要自建所有 provider transport。
- 后续若要移除 SDK、统一 HTTP/SSE substrate，必须新增 ADR，列出依赖减少收益、mock strategy、SSE/event schema 覆盖、错误兼容和迁移成本。

