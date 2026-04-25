---
doc_id: 019dc53b-c1e7-7626-b550-85ae1085deca
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T17:22:38+02:00
---
# 0009-pi-cli-product-equivalence-contract

- Status: accepted
- Date: 2026-04-25
- Roadmap: `design_docs/roadmap/p1_engine_pi.md`
- Architecture: `design_docs/architecture/p1_pi_cli_technical_architecture.md`

## 选了什么

- P1 的 Pi CLI 复刻路线采用 **产品体验等价 + contract-stable**。
- 产品体验等价：NeoMAGI CLI 应覆盖 Pi CLI 的核心用户工作流，包括 TUI 对话、slash commands、代码库工具、session、compaction、extensions、skills、settings 和 structured session export。
- Contract-stable：优先保持 Pi mono 的核心 contract 可对照、可测试、可迁移，包括 message、content block、assistant stream event、agent event、tool schema、extension hook、session entry、usage/cost schema 和 opaque continuation fields。
- 不追求逐行实现兼容，不承诺跟随 pi-mono 主线每个 commit 的实现细节或 UI 细节。
- `main@97a38bf6`（fetch 时间 2026-04-25）作为 P1 初始阅读和 fixture 基线，不作为永久兼容承诺；升级基线需要独立 ADR 评审，不在主分支上自动跟随。
- `pi-package` install/update/remove 子系统不进入 P1 core；可作为 P1 stretch 或后续阶段。
- `pi-share-hf` 不在 pi-mono 内，P1 不内建 Hugging Face 上传或外部发布流程；P1 只提供可被类似工具消费的 structured export schema。

## 为什么

- “1:1 复刻”容易混淆三个层级：逐行实现兼容、产品体验等价、contract 等价。P1 需要明确选择，否则 architecture 和验收会反复漂移。
- pi-mono 高频迭代，逐 commit 跟随会让 P1 变成追 upstream 的移植项目，影响 NeoMAGI 自身的 Postgres、policy、memory 和 Python 约束。
- 用户真正需要的是 Pi CLI 的核心工作流可用，而不是 Node.js 实现细节被逐行复制。
- Contract-stable 可以保留最有价值的兼容面：mock playback、session import/export、provider/agent/TUI 解耦、extension 生态迁移和测试夹具复用。
- 排除 pi-package 和 pi-share-hf 能把 P1 收敛在可交付的 local CLI 产品上，避免把分发生态和外部分享链路提前并入核心路径。

## 放弃了什么

- 方案 A：冻结 `main@97a38bf6` 后做行为和实现 1:1。
  - 放弃原因：成本高，且 P1 完成时 upstream 可能已显著变化。
- 方案 B：只复刻 contract，UI 和 extension 自由发挥。
  - 放弃原因：无法保证用户获得 Pi CLI 等价的核心产品体验。
- 方案 C：P1 同时交付 pi-package 和 pi-share-hf 类上传。
  - 放弃原因：它们属于分发/分享生态，不是本地 CLI 核心闭环。

## 影响

- roadmap 中不得再使用未限定的“1:1 复刻”作为验收口径。
- P1-M0 必须产出 behavior matrix 和 contract fixture，明确哪些 Pi 行为属于 core、stretch、out of scope。
- 架构文档必须把 Pi-compatible contract 与 NeoMAGI-native 实现分开描述。
- JSONL、Postgres、export/import 必须保留 opaque continuation fields，例如 `thinkingSignature`、`thoughtSignature`、`responseId`。
- NeoMAGI 可以增强 Pi 默认行为，例如 shell policy、audit、Postgres session truth，但这些增强项必须标注为 NeoMAGI-specific，不写成 Pi 原生能力。
- 子决策由独立 ADR 承担，不并入本 ADR：协议层 Python 类型选型、auth credential 存储位置、Pi 基线升级策略、Anthropic stealth tool naming 是否复刻、provider routing schema 复刻范围。
