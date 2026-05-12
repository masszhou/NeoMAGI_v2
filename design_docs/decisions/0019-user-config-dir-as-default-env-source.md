---
doc_id: 019e06bf-e05a-7027-a20a-86d10b7352e9
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-08T10:41:31+02:00
---
# 0019-user-config-dir-as-default-database-secret-source

- Status: accepted
- Date: 2026-05-08
- Related: `design_docs/decisions/0007-database-hard-dependency-fail-fast.md`
- Related: `design_docs/decisions/0018-package-neomagi-pi-as-monorepo-product-boundary.md`
- Amended by: `design_docs/decisions/0020-magipi-workspace-and-global-resource-layout.md`

## 选了什么

### 查找顺序

`magipi` 读取数据库配置时按来源整组选取，不按字段混搭。必需字段为
`DATABASE_HOST`、`DATABASE_PORT`、`DATABASE_USER`、`DATABASE_PASSWORD`、`DATABASE_NAME`；
`DATABASE_SCHEMA` 可选，默认 `neomagi`。

查找顺序固定为：

1. CLI 参数 `--env-file <path>`。显式文件必须存在且字段齐全，否则 fail-fast。
2. 当前 shell 已导出的 `DATABASE_*`。只要出现任一必需字段，就要求五项齐全，否则 fail-fast。
3. `NEOMAGI_ENV_FILE` 指向的文件。显式文件必须存在且字段齐全，否则 fail-fast。
4. 用户配置目录数据库 secret 文件：
   1. `$XDG_CONFIG_HOME/neomagi/secrets/database.env`（如果设置）；
   2. Windows：`%APPDATA%\neomagi\secrets\database.env`；
   3. Linux / macOS：`~/.config/neomagi/secrets/database.env`。
5. repo `.env`：仅当从 `__file__` 向上能找到 NeoMAGI repo marker（`.env_template` 加
   `packages/magipi/`）时启用。发布版 wheel / 非 editable install 没有 marker，
   直接跳过；editable repo install 仍可命中，作为开发 fallback。

自动文件来源不存在时跳过；文件存在但字段不全时 fail-fast。这样避免临时 export 的
`DATABASE_HOST` 和旧数据库 secret 里的密码 / 库名拼成意外连接。这个取舍延续 ADR-0007：
连错库比连不上更危险。

### macOS 路径偏离 HIG

macOS 上不走 Apple 推荐的 `~/Library/Application Support/`，而是和 Linux 一样用
`~/.config/neomagi/secrets/database.env`。NeoMAGI 面向开发者 CLI，dotfile 同步和跨机器一致性比平台
原生路径更重要；这也跟随 git、neovim、ripgrep、fd 等命令行工具的事实约定。

### `magipi config` 子命令

提供两条小命令辅助首次配置和排障。

**`magipi config init`**：把内置 database env 模板写入用户配置目录的
`secrets/database.env`。

- 模板作为 package resource 打包进 wheel：`packages/magipi/src/storage/templates/database.env.template`；
  运行期用 `importlib.resources` 读取，不依赖 `$REPO`。
- 默认不覆盖已有 `secrets/database.env`；加 `--force` 才覆盖，并先备份成 `<path>.bak`。
- Linux/macOS 上目录权限设成 `0700`，文件权限设成 `0600`。Windows 不显式 chmod。
- 模板只写占位符（如 `change-me`），不预填真实凭据。

**`magipi config path`**：打印当前实际生效的来源。

- `source=env` 表示来自 shell 环境变量；`source=file:<path>` 表示来自文件。
- 当前为 `source=env` 时，不把候选文件伪装成生效路径；可附
  `would-fall-back-to: <path>` 辅助排障。

文档默认提示从「`export NEOMAGI_ENV_FILE="$REPO/.env"`」改为「首次安装跑
`magipi config init`」。

## 为什么

- 通过 pypi / wheel 安装的用户没有 `$REPO`。强制 `export NEOMAGI_ENV_FILE` 不合理；
  用户配置目录下的明确 database secret 文件才是 CLI 工具的默认入口。
- 配置目录和安装路径互不影响：换 venv、重装、从本地切到 pypi，配置都不用动。
- macOS 也走 `~/.config/`：一份 dotfile 配置即可覆盖 Linux 和 macOS。
- 不读当前目录的 `.env`：用户 workspace 常有 Node / Django 等项目配置，误读会连错库。
  按 ADR-0007，「连错库」比「连不上」更危险。
- 保留 repo `.env` 这一层，让本地开发者继续按原来的方式用，迁移成本几乎为零。
- 保留 `NEOMAGI_ENV_FILE`，用于多配置切换、docker bind mount、临时改连接。

## 放弃了什么

- 方案 A：默认读取当前目录的 `.env`。
  - 放弃原因：用户 workspace 常有其他项目的 `.env`，误读会连错库。
- 方案 B：让 `NEOMAGI_ENV_FILE` 成为 wheel 安装的唯一入口。
  - 放弃原因：每个 shell 都要 export，体验差，也暴露安装路径。
- 方案 C：引入 `platformdirs`，走平台原生路径（macOS 用 `~/Library/Application Support/neomagi/`）。
  - 放弃原因：macOS GUI 应用路径不适合 dotfile 同步；新增运行时依赖收益不足。
- 方案 D：默认走 macOS 原生 `~/Library/Application Support/`，但允许 `XDG_CONFIG_HOME` 覆盖。
  - 放弃原因：默认路径仍偏离开发者 CLI 习惯，首次配置门槛更高。

## 影响

### 代码

- `packages/magipi/src/storage/config.py`：
  - 新增 `_user_config_dotenv_path()`：`XDG_CONFIG_HOME` 优先，Windows 用 `APPDATA`，
    其他平台用 `~/.config/neomagi/secrets/database.env`。
  - `_app_root_dotenv_path()` 只在找到 repo marker 时返回 repo `.env`；删除
    `module_path.parent / ".env"` fallback。
  - 解析改为整组取舍：显式来源缺失或不完整时 fail-fast；自动文件不存在时跳过，
    文件存在但不完整时 fail-fast；错误信息列出尝试来源和修复建议。
- `packages/magipi/src/storage/templates/database.env.template`：把现有 `.env_template` 复制成
  package resource，并在 `pyproject.toml` 中声明打包进 wheel；运行期用
  `importlib.resources` 读取。repo 根目录的 `.env_template` 仅供开发参考。
- `packages/magipi/src/cli/cli_args.py`：给 `CliOptions` 加 `env_file: Path | None`；
  `--env-file` 解析后传给 `load_database_config(env_file=...)`。
- `packages/magipi/src/cli/__main__.py`：注册 `magipi config init` / `path`；
  `init` 负责不覆盖、`--force` 备份、Unix `0700/0600` 权限。
- `DatabaseConfigError`：列出查找顺序，并提示 `magipi config init` / `path`。

### 测试

- `tests/storage/test_config.py`：
  - 覆盖默认 `~/.config/neomagi/secrets/database.env`、`XDG_CONFIG_HOME`、Windows `APPDATA` 三条路径。
  - 覆盖整组取舍：`NEOMAGI_ENV_FILE` 不被用户配置污染；部分 `DATABASE_*` 不和文件混搭；
    `--env-file` 压过完整 shell 环境。
  - 更新 package-dir fallback 用例：无 repo marker 时返回 sentinel，不退到 package 目录。
- `tests/cli/`：加 entrypoint 级用例，覆盖 `--env-file` 传递、`config path`
  来源展示、`config init` 写入 / 拒绝覆盖 / `--force` 备份 / Unix 权限。
- Wheel 安装 smoke：把 wheel 装进临时 venv，确保无 repo marker；运行
  `magipi config init`、`magipi --help`、`magipi config path`，证明模板已打进 wheel。

### 文档

- `dev_docs/cheatsheet.md`：删掉 wheel/install 章节里的 `export NEOMAGI_ENV_FILE`，把「首次配置」步骤改成跑 `magipi config init`。
- `dev_docs/user_tests/p1_m7_manual_test_plan.md` §1.3：更新 DB 配置查找顺序的描述。
- `dev_docs/logs/p1_m7_manual_smoke_findings.md`：在 M7-MANUAL-001 和最终判定段落补一个指向本 ADR 的 amendment 链接。
- `dev_docs/logs/post_p1_neomagi_pi_package_migration_closeout.md`：在 Risks 段标记这条风险已被本 ADR 解决。
