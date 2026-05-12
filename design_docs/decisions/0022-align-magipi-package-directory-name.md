---
doc_id: 019e1e0d-375f-7024-9494-e314c26e67ee
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-12T23:17:17+02:00
---
# 0022-align-magipi-package-directory-name

- Status: accepted
- Date: 2026-05-12
- Amends: `design_docs/decisions/0018-package-neomagi-pi-as-monorepo-product-boundary.md`
- Related: `design_docs/decisions/0020-magipi-workspace-and-global-resource-layout.md`

## 选了什么

- NeoMAGI repo 内 Pi-compatible agent shell 的 package directory 改为 `packages/magipi/`。
- `magipi` 是该 engine 的规范工程名：CLI command、workspace control dir `.magipi/`、global engine resources 与 repo directory 统一使用同一语义。
- 本决策只治理 repo directory 命名。第一轮迁移应保持运行时行为不变，优先更新 workspace 配置、构建脚本、测试、文档和路径引用。
- Python import namespace、distribution package name、wheel name 是否同步改名，不由本 ADR 强制；如需调整，应在迁移计划中单独列出影响面和验收证据。
- 迁移完成后，`packages/neomagi_pi/` 不再作为新的源码目录或兼容别名保留，除非后续发现真实外部使用需要短期迁移桥。

## 为什么

- P1 验收后，`magipi` 已经是用户实际运行和调试时看到的入口名；继续保留 `packages/neomagi_pi/` 会让 package boundary、CLI command 和资源目录出现两个名字。
- ADR-0020 已经把 `.magipi/` 和 global `neomagi/magipi/` 固化为 engine resource 命名；repo directory 继续使用 `neomagi_pi` 会制造不必要的文档和排障歧义。
- 现在还处在产品边界收敛期，尽早统一名称比后续在更多 package、脚本和报告里叠加兼容解释更低熵。
- 目录 rename 是可审计、可回滚的治理动作，不应该顺手扩大为 import API、distribution metadata 或运行时语义重构。

## 放弃了什么

- 方案 A：继续使用 `packages/neomagi_pi/`，只在文档里说明它对应 `magipi`。
  - 放弃原因：同一 engine 长期保留两个名字，会增加后续计划、脚本、测试和人工交接中的歧义。
- 方案 B：保留 `packages/neomagi_pi/` 作为 symlink、copy 或长期兼容别名。
  - 放弃原因：当前没有已发布用户依赖该目录路径；兼容层会让导入路径、构建路径和搜索结果更难判断。
- 方案 C：在同一决策中强制同步改 Python import namespace、distribution package name 和 wheel name。
  - 放弃原因：这些属于可见 API / packaging metadata，影响面大于 repo directory rename；需要单独按真实使用面校准。

## 影响

- ADR-0018 中 `packages/neomagi_pi/` 的 forward-looking package directory 口径由本 ADR 修订为 `packages/magipi/`。
- 后续实施计划应至少覆盖 repo workspace members、`justfile`、build/test commands、scripts、tests、README、package README、复杂度守卫路径和其他硬编码路径引用。
- `magipi` CLI command 保持规范入口；开发/测试期 `uv run python -m cli ...` 是否继续保留，按现有实施基线处理。
- 验收应证明目录 rename 后入口级行为不变，例如 `uv run magipi --help`、`uv run --package neomagi-pi magipi --help`、focused package tests 和构建 smoke。
