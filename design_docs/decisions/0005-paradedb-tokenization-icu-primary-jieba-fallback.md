---
doc_id: 019cbfbe-bf38-7983-8b72-efe0d3a9d07e
doc_id_format: uuidv7
doc_id_assigned_at: 2026-03-05T21:44:35+01:00
---
# 0014-paradedb-tokenization-icu-primary-jieba-fallback

- Status: accepted
- Date: 2026-02-16

## 选了什么
- 在 ParadeDB `pg_search` 的 BM25 索引中，采用 ICU + Jieba 的同列双 tokenizer 策略。
- ICU 作为主召回通道，Jieba 作为中文补充召回通道。

## 为什么
- 实测显示 ICU 在中英混排、中德混排、专业术语混排上更稳定。
- Jieba 在长中文文本和无空格中英紧邻文本上有明显补充价值。
- 单索引内双 tokenizer 能在不引入外部搜索系统的前提下提升覆盖面。

## 放弃了什么
- 方案 A：仅使用 Jieba。
  - 放弃原因：中英/中德混排稳定性不足，存在空 token 问题。
- 方案 B：仅使用 ICU。
  - 放弃原因：长中文文本分词效果不稳定，中文召回会受损。
- 方案 C：引入外部搜索系统（OpenSearch/Elasticsearch）作为主检索。
  - 放弃原因：当前阶段运维复杂度过高，不符合最小化实现原则。

## 影响
- 该策略已在 PostgreSQL + ParadeDB 扩展环境中验证；项目当前数据库版本基线见 ADR 0046（PostgreSQL 17）。
- 建索引时，Jieba 字段开启 `trim=true` 以减少空白 token 干扰。
- 查询时采用显式权重策略，确保 ICU 分数权重大于 Jieba，避免重复命中过度加分。
- 推荐查询权重基线：`title(icu)=2.0`、`content(icu)=1.0`、`content_jieba=0.7`（可按评测微调）。

## 参考实现（SQL）
```sql
CREATE INDEX articles_search_idx ON articles
USING bm25 (
    id,
    (title::pdb.icu),
    (content::pdb.icu),
    (content::pdb.jieba('alias=content_jieba', 'trim=true')),
    category
)
WITH (key_field = 'id');
```

```sql
SELECT id, pdb.score(id) AS score
FROM articles
WHERE title ||| $1::pdb.boost(2.0)
   OR content ||| $1::pdb.boost(1.0)
   OR content::pdb.alias('content_jieba') ||| $1::pdb.boost(0.7)
ORDER BY score DESC
LIMIT 50;
```
