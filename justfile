# Run linter checks 做门禁检查，只拦“新增或恶化”的 block 问题
lint:
    uv run ruff check src/
    uv run python -m infra.complexity_guard check

# Show current complexity snapshot 看当前全仓快照
complexity-report:
    uv run python -m infra.complexity_guard report

# Refresh ratchet baseline after an intentional cleanup pass 完成一轮明确治理后，刷新 baseline
complexity-baseline:
    uv run python -m infra.complexity_guard write-baseline

# Ensure markdown files have the standard doc_id front matter 为 markdown 文件补标准头
md-doc-header path:
    uv run python scripts/upsert_md_doc_header.py "{{path}}"
