# Run linter checks 做门禁检查，只拦“新增或恶化”的 block 问题
lint:
    uv run ruff check packages/magipi/src packages/webui/src tests packages/webui/tests scripts
    uv run python -m infra.complexity_guard check

# Run WebUI package tests 运行 WebUI 包测试
webui-test:
    uv run --package neomagi-webui pytest packages/webui/tests tests/storage/test_audit_read_models.py

# Start the local WebUI dashboard 启动本地 WebUI dashboard
webui-dev:
    uv run --package neomagi-webui magipi-webui serve

# Show current complexity snapshot 看当前全仓快照
complexity-report:
    uv run python -m infra.complexity_guard report

# Refresh ratchet baseline after an intentional cleanup pass 完成一轮明确治理后，刷新 baseline
complexity-baseline:
    uv run python -m infra.complexity_guard write-baseline

# Ensure markdown files have the standard doc_id front matter 为 markdown 文件补标准头
md-doc-header path:
    uv run python scripts/upsert_md_doc_header.py "{{path}}"

# Create missing local Postgres session schema objects 创建缺失的本地 session schema/table
db-session-ensure:
    uv run python scripts/session_db.py ensure

# Show local Postgres session schema metadata 查看本地 session schema metadata
db-session-status:
    uv run python scripts/session_db.py status

# Drop and recreate the configured local session schema 重建本地测试 session schema（会删数据；需 --yes）
db-session-reset confirm="":
    uv run python scripts/session_db.py reset {{confirm}}
