---
doc_id: 019dc60b-e85f-717e-b373-84738c5827f0
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T21:09:19+02:00
---
# Logs

里程碑回顾性记录。每次 milestone 收尾时把"per-W 状态、与 plan 的偏离、upstream
observed but deferred、下一里程碑前置条件"等内部追踪细节落到本目录。

## 与其他目录的边界

| 目录 | 时间维度 | 内容 |
| --- | --- | --- |
| `dev_docs/plans/` | 前瞻 | 本里程碑要做什么（plan）。**不放完成后产物**。 |
| `dev_docs/logs/` | 回顾 | 本里程碑的 closeout 报告：每条 W 的状态、偏离、deferred、handoff checklist。 |
| `dev_docs/progress/progress.md` | 全局总账 | append-only；每次里程碑收尾追加一条引用 `dev_docs/logs/<milestone>_closeout.md` 的总结条目。 |

## 命名

- `<milestone_id>_closeout.md`，例如 `p1_m0_closeout.md`、`p1_m1_closeout.md`。
- 与同名 plan 文件 `dev_docs/plans/<milestone_id>_*.md` 一对一。

## 写作约定

- 触发时机：里程碑全部 W 项验收通过当天写入；不再回写历史 closeout。
- 不可变：closeout 一经入库视为冻结快照；后续里程碑发现更准确的事实通过新的
  closeout 或 ADR 修正，不回改旧文件。
- 可追溯：每条 W 行尽量带 commit hash / PR 编号；偏离段写清原因 + 影响。
- 与 `progress.md` 的关系：closeout 是详账，`progress.md` 只追加一行引用 closeout
  的摘要条目。
- doc_id：通过 `just md-doc-header dev_docs/logs/<file>.md` 添加 UUID 头。
