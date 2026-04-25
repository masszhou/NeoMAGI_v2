---
doc_id: 019c6e0d-9998-75c6-a05c-9f7392331fa5
doc_id_format: uuidv7
doc_id_assigned_at: 2026-02-18T01:01:51+01:00
---
# 0017-database-schema-default-neomagi

- Status: accepted
- Date: 2026-02-17

## 选了什么
- 决议默认业务 schema 为 `neomagi`，即 `DB_SCHEMA=neomagi`（实现变量名为 `DATABASE_SCHEMA`）。
- 不使用 `public` 作为 NeoMAGI 业务表默认 schema。
- 连接 `search_path` 约定为 `neomagi, public`，业务对象必须落在 `neomagi`。
- `public` 仅用于 PostgreSQL 扩展与兼容对象，不承载 NeoMAGI 业务表。

## 为什么
- `neomagi` 作为独立命名空间可降低命名冲突与误操作风险。
- 有利于最小权限授权：应用账号权限聚焦在 `neomagi`，安全边界更清晰。
- 迁移、备份、恢复可按 schema 粒度操作，运维与排障更可控。
- 可消除“配置与实现 schema 不一致”带来的静默退化风险。

## 放弃了什么
- 方案 A：继续使用 `public` 作为默认业务 schema。
  - 放弃原因：边界模糊，长期权限治理与维护风险更高。
- 方案 B：在不同环境自由切换 `public` 与 `neomagi`。
  - 放弃原因：增加测试矩阵和排障复杂度，不符合 MVP 简化原则。
- 方案 C：当前阶段引入多业务 schema 并行。
  - 放弃原因：收益不足，会增加迁移和运维复杂度。

## 影响
- 配置模板与文档默认值统一为 `DATABASE_SCHEMA=neomagi`。
- 初始化与迁移流程需在建表前执行 `CREATE SCHEMA IF NOT EXISTS neomagi`。
- 应用启动应做 schema 一致性自检，不一致时快速失败（fail fast）。
- 若未来改回 `public` 或启用多 schema，需新增决策并将本条标记为 superseded。
