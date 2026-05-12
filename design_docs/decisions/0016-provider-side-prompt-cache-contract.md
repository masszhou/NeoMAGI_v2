---
doc_id: 019dcb9d-855b-768c-8bd4-02acba2db871
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-26T23:06:31+02:00
---
# 0016-provider-side-prompt-cache-contract

- Status: accepted
- Date: 2026-04-26
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Related: `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md`
- Roadmap: `design_docs/roadmap/p1_engine_pi.md` § P1-M2

## 选了什么

- NeoMAGI M2 把 prompt cache 定义为 provider-side optimization，不在本地存 prompt cache 内容、prefix key、cache entry 或 eviction state。
- M2 统一保留 `Usage.cacheRead/cacheWrite`，并把 provider 返回的 cached token usage 归一化到这两个字段。
- Anthropic Messages prompt cache 采用 pi-mono 的简化版本：
  - 给 Anthropic Messages API 请求中的 prompt 片段打 `cache_control` 标记；
  - system prompt block 加 `cache_control`；
  - tools 存在时只给最后一个 tool definition 加 `cache_control`；
  - messages 转换后，只给最后一个 `user` message 的最后一个可标记 block 加 `cache_control`；
  - 如果最后一个 user content 是 string，转成 text block array 后再加标记；
  - direct `api.anthropic.com` + long retention 可以使用 Anthropic 支持的长 TTL；proxy 不假设支持长 TTL。
- Anthropic cache hit/write 只来自 Anthropic 返回的 usage：
  - `cache_read_input_tokens` -> `Usage.cacheRead`；
  - `cache_creation_input_tokens` -> `Usage.cacheWrite`。
- OpenAI prompt cache 对齐 pi-mono 的 direct OpenAI 请求级策略：
  - 不做本地缓存；
  - 不手动给 OpenAI prompt block 打 `cache_control`；
  - 只传 provider 支持的请求级 cache affinity，例如 `prompt_cache_key`、`prompt_cache_retention` 和必要的 session affinity headers；
  - `cacheRetention="none"` 时不发送任何 cache/session affinity 字段。
- OpenAI cached token usage 归一化为：
  - Responses API: `input_tokens_details.cached_tokens` -> `Usage.cacheRead`，`Usage.cacheWrite=0`；
  - Chat Completions / compatible usage: `prompt_tokens_details.cached_tokens` -> `Usage.cacheRead`，`cache_write_tokens` -> `Usage.cacheWrite`；
  - 如果兼容 provider 把 current write 也计入 cached tokens，先扣除 `cache_write_tokens` 后再写入 `Usage.cacheRead`。

## 为什么

- Prompt cache 是 provider 的计费和延迟优化，不是 NeoMAGI 的 durable memory truth。把 cache 内容落到本地会把 provider optimization 误建模成产品状态。
- NeoMAGI 已有持久记忆、session、audit 和未来 Postgres truth 边界；prompt cache 不应混入这些长期状态。
- pi-mono 的 Anthropic 实现足够支持 M2 最小闭环：标记 system / last tool / last user message，让 Anthropic 自己决定 cache hit/write，并从 usage 回传结果。
- OpenAI 的 cache 语义和 Anthropic 不同。OpenAI direct API 使用请求级 `prompt_cache_key` / `prompt_cache_retention`，强行套 `cache_control` block 会制造错误抽象。
- `Usage.cacheRead/cacheWrite` 是 NeoMAGI 对外稳定 contract；provider-specific cache 字段只在 adapter 内部存在。
- 保持 provider-side cache 可以让 M2 验收集中在 payload、stream usage 和 cost normalization，而不是实现本地 cache invalidation。

## 放弃了什么

- 方案 A：NeoMAGI 本地保存 prompt cache 内容或 prefix key。
  - 放弃原因：这会把 provider cache 当成本地 truth，引入隐私、驱逐、失效和跨 provider 语义问题。
- 方案 B：复刻 Claude Code 的复杂 Anthropic cache strategy。
  - 放弃原因：M2 只需要 direct API key 路径的最小可用闭环；Claude Code OAuth、identity prompt、stealth tool mapping 和多段 cache 策略属于后续范围。
- 方案 C：对 OpenAI prompt block 使用 Anthropic-style `cache_control`。
  - 放弃原因：direct OpenAI cache 是请求级 affinity；block-level `cache_control` 是 Anthropic / 部分兼容 provider 的独立语义，不进入 M2 OpenAI provider contract。
- 方案 D：把 cache miss 作为错误或回退触发条件。
  - 放弃原因：cache miss 是正常 provider 行为，只影响成本和延迟，不改变模型语义。

## 影响

- `packages/magipi/src/ai_provider` 的 usage model 必须保留 `input/output/cacheRead/cacheWrite/totalTokens/cost` 五维结构。
- `Usage.input` 表示非缓存输入 token；`totalTokens` 必须等于 `input + output + cacheRead + cacheWrite`，除非 provider 明确返回更可靠的 total 且 adapter 已记录原因。
- M2 provider adapter 必须把 prompt cache 字段限制在 provider request boundary 内，不向 session/memory/storage 层暴露 provider cache 内容。
- `cacheRetention` 统一取值为 `none | short | long`，默认 `short`；`PI_CACHE_RETENTION=long` 可以把默认提升为 `long`；显式 option 优先。
- `cacheRetention="none"` 是硬禁用：不得发送 `prompt_cache_key`、`prompt_cache_retention`、`cache_control`、`cachePoint`、`session_id`、`x-client-request-id`、`x-session-affinity` 等 cache/session affinity 字段。
- M2 测试需要覆盖 Anthropic `cache_control` payload、OpenAI request-level cache payload、raw usage 到 `Usage.cacheRead/cacheWrite` 的归一化。
- 后续若要支持 OpenAI-compatible provider 的 Anthropic-style `cache_control` opt-in，必须作为兼容 provider 行为单独记录，不改变 direct OpenAI provider contract。

