---
doc_id: 019dc593-4e84-7169-bba5-e8ecb422f4fc
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T18:56:31+02:00
---
# 0010-use-pydantic-v2-for-protocol-types

- Status: accepted
- Date: 2026-04-25
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Roadmap: `dev_docs/plans/p1_m0_pi_baseline_and_fixtures.md`

## 选了什么

- P1 协议层和跨包边界的 Python 类型统一采用 `pydantic` v2。
- 覆盖范围包括 message、content block、assistant stream event、agent event、tool definition、session entry、JSONL import/export、usage/cost、extension hook payload 和 settings 中需要跨模块传递或持久化的 schema。
- Pi-compatible wire model 必须保持 Pi 字段名和 JSON 形状；Python 内部可以使用 snake_case 字段，但序列化必须通过 alias 输出 Pi-compatible casing。
- Pi-compatible model 必须显式保留未知字段和 opaque continuation metadata，例如 `thinkingSignature`、`thoughtSignature`、`textSignature`、`responseId`。不得因为 `extra="forbid"` 丢弃或拒绝这些字段。
- Discriminated union 优先使用协议中已有的判别字段，例如 `type`、`role`、`entry_type`。实现应暴露 `TypeAdapter` 或等价入口用于 fixture round-trip、provider adapter 边界和 JSONL import/export 校验。
- Tool argument schema 仍以 JSON Schema 为跨语言 contract；`pydantic` 负责 NeoMAGI 内建类型、边界 payload 和序列化校验，不替代 extension 提供的 JSON Schema。

## 为什么

- P1 选择 product-equivalent + contract-stable 后，类型层必须同时服务 mock fixture、provider adapter、TUI playback、session JSONL、Postgres projection 和 extension API，单纯静态类型不足以覆盖这些运行时边界。
- `pydantic` v2 提供成熟的运行时校验、别名序列化、discriminated union、JSON Schema 导出和 `TypeAdapter`，能把 Pi-compatible contract 变成可测试对象。
- Fixture round-trip、JSONL import/export 和 provider 输入输出校验是 P1 的核心验收路径；这些路径需要可复用的解析与序列化规则，而不是每处手写校验。
- 相比极致性能，当前阶段更需要 contract 正确、错误可解释、迁移成本低。`pydantic` v2 的运行时成本在 P1 的 CLI / agent 边界可接受。

## 放弃了什么

- 方案 A：`TypedDict` + 手写校验。
  - 放弃原因：静态类型轻量，但运行时校验、别名序列化、union 判别和错误报告都需要重复实现；容易让 fixture、provider 和 JSONL import 形成多套规则。
- 方案 B：`msgspec`。
  - 放弃原因：性能优势明显，但 P1 的主要风险不是 JSON 编解码吞吐，而是 Pi-compatible 字段保真、未知字段透传、错误解释和生态熟悉度；当前阶段收益不足以抵消迁移和认知成本。
- 方案 C：`dataclasses` / `attrs` 作为协议类型核心。
  - 放弃原因：适合内部领域对象，但跨边界 JSON contract 仍需另配校验、alias、schema 和 union 解析机制。

## 影响

- `pydantic>=2,<3` 成为 P1 runtime dependency；后续实现应在 `pyproject.toml` 中加入生产依赖，而不是仅放入 dev dependency。
- `neomagi_ai.types`、`neomagi_agent_core.types`、`neomagi_cli.core.session_types`、`neomagi_cli.extensions.types` 等协议类型模块应以 `pydantic` v2 model / adapter 为主。
- Pi-compatible model 默认策略是保守透传：允许未知字段、保留 opaque metadata、按 Pi-compatible alias dump。只有 NeoMAGI 内部私有模型可以在确认无兼容要求后使用 `extra="forbid"`。
- 测试必须覆盖至少一个核心 `AssistantMessage`、一个 `AssistantMessageEvent`、一个 session entry 和一个 JSONL export/import 的 round-trip，确保未知字段不会丢失。
- 若后续实测发现高频 stream event 校验成为性能瓶颈，可以在事件生成热路径做局部优化，但不得改变边界 contract 和 fixture/import/export 的 `pydantic` 校验入口。
