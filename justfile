# NeoMAGI_v2 — task runner (https://just.systems)
# Usage: just <recipe>

# Paths fed to ruff (check / format / fix) lint 与格式化覆盖的路径
lint_paths := "packages/magipi/src packages/webui/src tests packages/webui/tests scripts"
# Container compose driver; override with `just compose="docker compose" ...` 容器编排驱动，可覆盖
compose := "podman compose"
# Direct container CLI for exec; override with `just container_cli=docker ...` 直接进容器的 CLI，可覆盖
container_cli := "podman"
postgres_container := "neomagi-postgres"

# List all recipes 列出全部 recipe
default:
    @just --list

# ── Setup ──────────────────────────────────────────────────────────────────

# Sync the workspace (core + dev deps) 同步工作区依赖（含 dev 组）
install:
    uv sync

# Copy the env template (run once after cloning) 复制 env 模板（克隆后跑一次）
env:
    cp -n .env_template .env
    @echo ".env created — fill in DATABASE_* before running the stack"

# ── Development ────────────────────────────────────────────────────────────

# Run the P1 CLI shell; pass args after the recipe 启动 P1 CLI；参数跟在后面
cli *args:
    uv run python -m cli {{args}}

# Start the local WebUI dashboard 启动本地 WebUI dashboard
webui-dev:
    uv run --package neomagi-webui magipi-webui serve

# ── Code quality ───────────────────────────────────────────────────────────

# Run the gate: ruff + complexity ratchet 门禁：ruff + 复杂度棘轮（只拦新增/恶化）
lint:
    uv run ruff check {{lint_paths}}
    uv run python -m infra.complexity_guard check

# Format with ruff 用 ruff 格式化
format:
    uv run ruff format {{lint_paths}}

# Auto-fix lint + format issues 自动修复 lint 与格式问题
fix:
    uv run ruff check --fix {{lint_paths}}
    uv run ruff format {{lint_paths}}

# Show current complexity snapshot 看当前全仓复杂度快照
complexity-report:
    uv run python -m infra.complexity_guard report

# Refresh ratchet baseline after an intentional cleanup pass 明确治理后刷新 baseline
complexity-baseline:
    uv run python -m infra.complexity_guard write-baseline

# ── Testing ────────────────────────────────────────────────────────────────

# Run the core (neomagi-pi) test suite 运行核心包测试
test:
    uv run pytest tests

# Run WebUI package tests 运行 WebUI 包测试
webui-test:
    uv run --package neomagi-webui pytest packages/webui/tests tests/storage/test_audit_read_models.py

# Run every test suite 跑全部测试
test-all: test webui-test

# ── Database ───────────────────────────────────────────────────────────────

# Start the local Postgres (ParadeDB) stack 启动本地 Postgres（ParadeDB）栈
db-up:
    {{compose}} -f docker-compose.yml up -d

# Stop the local Postgres stack (keeps the volume) 停止本地 Postgres 栈（保留卷）
db-down:
    {{compose}} -f docker-compose.yml down

# Tail Postgres container logs 跟踪 Postgres 容器日志
db-logs:
    {{compose}} -f docker-compose.yml logs -f postgres

# Dump the local NeoMAGI Postgres schema to a portable archive 导出本地 NeoMAGI Postgres schema
db-dump output="tmp/neomagi.dump" schema="neomagi":
    @mkdir -p "$(dirname "{{output}}")"
    {{container_cli}} exec -i {{postgres_container}} sh -lc 'set -eu; schema="$1"; case "$schema" in ""|[0-9]*|*[!A-Za-z0-9_]*) echo "schema must be a simple PostgreSQL identifier" >&2; exit 2;; esac; pg_dump --format=custom --no-owner --no-privileges --schema "$schema" -U "$POSTGRES_USER" -d "$POSTGRES_DB"' sh "{{schema}}" > "{{output}}"
    @echo "Wrote {{output}}"

# Restore a NeoMAGI Postgres schema dump; drops the target schema 还原 NeoMAGI schema dump（会先删除目标 schema；需 --yes）
db-restore input confirm="" schema="neomagi":
    @test "{{confirm}}" = "--yes" || (echo "Refusing to restore without --yes: just db-restore {{input}} --yes" >&2; exit 2)
    @test -f "{{input}}" || (echo "Dump file not found: {{input}}" >&2; exit 2)
    {{container_cli}} exec -i {{postgres_container}} sh -lc 'set -eu; schema="$1"; case "$schema" in ""|[0-9]*|*[!A-Za-z0-9_]*) echo "schema must be a simple PostgreSQL identifier" >&2; exit 2;; esac; psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA IF EXISTS \"$schema\" CASCADE"; pg_restore --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' sh "{{schema}}" < "{{input}}"
    @echo "Restored {{input}} into schema {{schema}}"

# Create missing local session schema objects 创建缺失的本地 session schema/table
db-session-ensure:
    uv run python scripts/session_db.py ensure

# Show local session schema metadata 查看本地 session schema metadata
db-session-status:
    uv run python scripts/session_db.py status

# Drop and recreate the local session schema 重建本地 session schema（会删数据；需 --yes）
db-session-reset confirm="":
    uv run python scripts/session_db.py reset {{confirm}}

# ── Docs ───────────────────────────────────────────────────────────────────

# Ensure a markdown file has the standard doc_id front matter 为 markdown 文件补标准头
md-doc-header path:
    uv run python scripts/upsert_md_doc_header.py "{{path}}"

# ── Maintenance ────────────────────────────────────────────────────────────

# Remove cache / compiled files 清理缓存与编译产物
clean:
    find . -type d -name __pycache__ -not -path "./.venv/*" | xargs rm -rf
    find . -type d -name .pytest_cache -not -path "./.venv/*" | xargs rm -rf
    find . -type d -name .ruff_cache -not -path "./.venv/*" | xargs rm -rf
    find . -name "*.pyc" -not -path "./.venv/*" -delete
    @echo "Cleaned."
