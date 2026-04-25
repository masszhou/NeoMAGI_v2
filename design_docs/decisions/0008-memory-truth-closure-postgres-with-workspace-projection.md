---
doc_id: 019dc3a5-6399-7795-9662-50ce32fec867
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T09:57:09+02:00
---
# 0008-memory-truth-closure-postgres-with-workspace-projection

- Status: accepted
- Date: 2026-04-25

## 选了什么
- Postgres memory ledger 是生产 daily path 中机器写入 memory 的唯一真源。
- Workspace Markdown memory 文件是 human-readable projection / export，不是写入真源。
- `memory_append` 写入顺序为：
  1. 先写 DB；
  2. DB 成功后同步 append 到 Markdown projection；
  3. projection 失败不回滚 DB。
- `memory_entries` 是 retrieval projection；增量索引由 ledger write 驱动。
- Markdown projection 可以从 DB 重建。
- Projection 文件必须标注：`This file is auto-generated. Manual edits will be lost.`
- 手工文件编辑如需进入 memory truth，必须通过显式 import / reconcile 命令，不自动生效。

## 为什么
- DB 更适合承载 stable id、scope、principal、visibility、provenance、metadata 和审计。
- Workspace 文件的核心价值是可读、可导出、可重建，而不是承载并发写入和授权裁决。
- 文件与 DB 双主会引入复杂 reconcile 语义
- Projection 失败不影响 DB truth，能避免用户可读面的问题破坏记忆写入闭环。

## 放弃了什么
- 方案 A：长期保持 DB 与 Markdown 双写双主。
  - 放弃原因：冲突语义不闭合，且会让用户直接文件编辑绕过授权和审计。
- 方案 B：DB 写入失败时仍写 Markdown。
  - 放弃原因：会重新制造文件真源，破坏单一 truth。
- 方案 C：Markdown projection 失败时回滚 DB。
  - 放弃原因：projection 是可重建展示面，不应阻断真源写入。

## 影响
- `memory_append` 与后续 memory writer 继续以 DB 写入成功作为 truth 判定。
