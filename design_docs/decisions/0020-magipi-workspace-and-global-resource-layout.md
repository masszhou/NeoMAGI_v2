---
doc_id: 019e1173-43af-7475-a0e5-d44a3826821f
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-10T12:33:37+02:00
---
# 0020-magipi-workspace-and-global-resource-layout

- Status: accepted
- Date: 2026-05-10
- Related: `design_docs/decisions/0006-database-schema-default-neomagi.md`
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Related: `design_docs/decisions/0018-package-neomagi-pi-as-monorepo-product-boundary.md`
- Related: `design_docs/decisions/0019-user-config-dir-as-default-env-source.md`
- Related: `design_docs/decisions/0021-workspace-materialized-skills-and-env-grants.md`
- Amended by: `design_docs/decisions/0021-workspace-materialized-skills-and-env-grants.md`

## 选了什么

NeoMAGI 采用 NeoMAGI-native 的 workspace / global resource 与 secret 布局，不再保留
Pi 兼容路径作为隐式读取来源。本 ADR 固化并承接 ADR-0019 的 database secret 命名：
数据库连接信息属于全局 secret，不使用含糊的 root-level `.env` 文件名。

### Naming boundary

`NeoMAGI` 是产品名：personal digital assistant、数据库 schema、用户配置根目录都跟随
NeoMAGI 命名。`magipi` 是 NeoMAGI 内的 Pi-like agent engine / CLI 入口：workspace 控制目录和
全局 engine resource 目录使用 magipi 命名。

`skill_pool` 属于 NeoMAGI 产品层候选能力库，不属于 magipi runtime/global resource。Pool 中的
skill 不 provider-visible；只有 materialized 到当前 workspace `.magipi/skills/` 的 skill 才能进入
magipi prompt 和 skill env grant 流程。

因此路径命名固定为：

- product-level user config root: `neomagi/`
- product-level skill pool: `neomagi/skill_pool/`
- global magipi engine resources, excluding skills: `neomagi/magipi/`
- workspace-local magipi control dir: `.magipi/`

### Workspace resource root

当前 workspace 的项目级控制目录固定为 `.magipi/`：

- `.magipi/settings.json`
- `.magipi/skills/`
- `.magipi/extensions/`
- `.magipi/prompts/`
- `.magipi/themes/`
- `.magipi/secrets/`

项目 settings 中的相对路径按 workspace root 解析；workspace-local secret 文件应放在
`.magipi/secrets/` 下。`magipi` 创建 `.magipi/` 时应写入 `.magipi/.gitignore`，至少包含
`secrets/`。

示例：

```json
{
  "resources": {
    "skillEnv": {
      "brave-search": {
        "envFile": ".magipi/secrets/brave-search.env",
        "allow": ["BRAVE_API_KEY"]
      }
    }
  }
}
```

### User config root and global resources

NeoMAGI 用户配置根目录固定为：

- `$XDG_CONFIG_HOME/neomagi/`，如果设置了 `XDG_CONFIG_HOME`
- Windows `%APPDATA%\neomagi\`
- Linux / macOS `~/.config/neomagi/`

目录结构：

```text
neomagi/
  magipi/
    settings.json
    extensions/
    prompts/
    themes/
  skill_pool/
  auth.json
  secrets/
    database.env
```

`magipi/` 只保存全局 magipi atomic-tool/resource settings，不保存全局 skills。全局 settings 是
fallback；project settings 覆盖同名 resource。`skill_pool/` 是 NeoMAGI 产品层候选能力库，不被
runtime 扫描进 `<available_skills>`。`resources.skillEnv` 的 runtime 语义由 ADR-0021 定义。
ADR-0021 进一步收紧 skill runtime：只有 workspace materialized skill 是 active skill source，
不引入 workspace 外 skill read / bash path 例外，`/skill:<name>` 仅保留为 development/debug shortcut。

### Global secrets

数据库连接信息固定为用户配置根目录下的 `secrets/database.env`：

- `$XDG_CONFIG_HOME/neomagi/secrets/database.env`
- Windows `%APPDATA%\neomagi\secrets\database.env`
- Linux / macOS `~/.config/neomagi/secrets/database.env`

`secrets/database.env` 是 ADR-0019 中 database secret 的规范文件名。它只保存
`DATABASE_*` 连接信息，不保存 provider token、OAuth token 或 skill-specific API key。
`magipi config init` 应写入这个文件；`magipi config path` 应报告这个明确路径。

Skill-specific API key env files 默认随 workspace skill 放在 `.magipi/secrets/`，由
workspace `.magipi/settings.json` 引用。NeoMAGI 产品层以后如需管理 skill pool secret inventory，
必须另写 ADR；本 ADR 不定义全局 active skill secret 目录。

`secrets/` 下所有文件按 secret 文件处理。平台支持时，目录权限应为 `0700`，文件权限应为
`0600`；Windows 不显式 chmod。

### Auth and OAuth naming

Provider credential storage 固定在用户配置根目录的 root-level `auth.json`：

- `$XDG_CONFIG_HOME/neomagi/auth.json`
- Windows `%APPDATA%\neomagi\auth.json`
- Linux / macOS `~/.config/neomagi/auth.json`

`auth.json` 是 provider API key 与 OAuth credential 的统一 structured store：它是
程序化读写的多 entry JSON，有 `type` 等结构字段。`secrets/*.env` 是纯文本环境变量赋值文件。
因此 OAuth token 不放进 workspace、不放进 `.magipi/`、不放进 `magipi/` resource 目录、
不放进 `secrets/database.env`，也不改放到 `secrets/auth.json`。

Credential entry key 使用稳定 provider credential id，例如 `openai-codex`。同一 provider
family 可能有多个 credential lane（例如普通 API key 与 Codex OAuth），entry key 不直接等同于
provider family 名；auth channel 由 entry 的 `type` 字段表达，例如 `{"type": "oauth", ...}`。
不要把 model reference 里的 channel path（如 `openai/oauth/...`）用作文件名、目录名或 auth
storage key。

## 为什么

- NeoMAGI 是总产品，对标 personal digital assistant；magipi 是其中的 agent engine，对标
  Pi agent。用户配置根跟产品名，workspace 和全局 engine resources 跟 engine 名，避免两者混用。
- `.pi` / `~/.pi` 是 Pi mono 的路径语义。NeoMAGI 是 Python-native `magipi` 产品，继续沿用
  `.pi` 会制造“兼容但不完全兼容”的长期歧义。
- root-level `.env` 无法表达它只服务数据库连接，容易和 workspace 应用 `.env`、skill env files、
  provider credentials 混淆。`secrets/database.env` 更准确。
- 当前还未上线，不需要维护 legacy 迁移路径；一次性改到干净布局比叠加兼容层更低熵。
- 集中管理仍有价值，但这里限于 non-skill global defaults 和 NeoMAGI 产品层 inventory，不是 active
  global skills。项目特定资源和 secret 引用仍由 project settings 明确覆盖。
- Active global skill 会把 workspace 外能力直接暴露给 provider，secret 泄漏面过大。候选 skill pool 留在
  NeoMAGI 产品层，materialized 后才进入 workspace，可以保持 runtime 边界简单。

## 放弃了什么

- 方案 A：继续隐式读取 `.pi/`、`~/.pi/agent/`、`.agents/skills/`。
  - 放弃原因：这些路径代表外部 harness 的兼容语义。当前没有上线用户，兼容层只会增加调试歧义。
- 方案 B：把全局 magipi resources 放在 `neomagi/agent/`。
  - 放弃原因：`agent/` 容易被读成某个 agent 实体的状态目录；`magipi/` 更准确表达 engine resource root。
- 方案 C：把所有 skill key 集中放在全局 `~/.config/neomagi/secrets/`，project 只引用 skill。
  - 放弃原因：workspace 难以自解释，多个项目使用同名 skill 时容易误用同一个 key。
- 方案 D：继续把数据库连接信息默认放在用户配置根目录的 `.env`。
  - 放弃原因：名称过宽，和普通项目 `.env`、skill env file、provider credential store 的边界不清。
- 方案 E：把 OAuth / API credentials 也放进 `secrets/*.env`。
  - 放弃原因：provider credentials 是 structured store，多 entry、可 list/status/delete/refresh；
    env files 更适合简单变量赋值，不适合 OAuth token lifecycle。

## 影响

- 项目 settings 解析逻辑改为 `<cwd>/.magipi/settings.json`。
- global magipi resource dir 改为 NeoMAGI app config dir 下的 `magipi/`，不再使用 `~/.pi/agent`、
  `~/.config/neomagi/agent` 或 `NEOMAGI_AGENT_DIR` 作为默认产品路径；测试需要隔离时可保留测试专用 override。
- Resource discovery 根改为 workspace `.magipi/{skills,extensions,prompts,themes}` 和 global
  `magipi/{extensions,prompts,themes}`；global `magipi/skills` 不作为 active resource root；
  删除 `.pi`、`~/.pi`、`.agents` 的隐式扫描。
- database config 的默认用户配置文件从 root-level `.env` 改为 `secrets/database.env`；
  `magipi config init/path`、内置模板和 ADR-0019 相关实现说明需要随之更新。
- auth storage 保持 root-level `auth.json`；OAuth credential entry key 使用 provider credential id，
  `type` 字段表达 `oauth`，不从 model reference 派生路径名。
- `resources.skillEnv` 保留当前 `envFile` + `allow` schema；runtime 只为当前 workspace 中已
  materialized 的 skill 解析 project-level env grant。
- 文档、fixtures、manual smoke 和生成示例从 `.pi` 改为 `.magipi`。
- `.magipi/` 初始化应写入 `.magipi/.gitignore`，至少包含 `secrets/`。真实 secret 不进入 Git。
