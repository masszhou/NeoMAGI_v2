---
doc_id: 019e118d-8830-70b5-99c8-f9471fab065a
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-10T13:02:13+02:00
---
# 0021-workspace-materialized-skills-and-env-grants

- Status: accepted
- Date: 2026-05-10
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Related: `design_docs/decisions/0012-python-native-extension-mvp-boundary.md`
- Related: `design_docs/decisions/0020-magipi-workspace-and-global-resource-layout.md`
- Amends: `design_docs/decisions/0020-magipi-workspace-and-global-resource-layout.md` § Naming boundary, § User config root and global resources, § Global secrets

## 选了什么

Skill 仍是 internal capability package，不是新的 provider tool 或独立 executor。当前 workspace
（见 ADR-0020 § Workspace resource root）启动或 resource reload 后，system prompt 暴露
`<available_skills>` 轻量摘要。正常用户路径是：用户用自然语言描述任务，model 根据摘要选择匹配
skill，用普通 atomic `read` 加载 `SKILL.md`，再用普通 atomic tools 完成任务。

当前阶段不允许 active global skills：global magipi 层只提供 atomic tools / non-skill resources。NeoMAGI
产品层可以有 `skill_pool` 作为候选能力库，但 pool 中的 skill 不 provider-visible，不进入
`<available_skills>`，也不能直接触发 secret grant。

只有 materialized 到当前 workspace `.magipi/skills/<skill-name>/` 的 skill 才是 runtime skill。
Materialization 的结果必须是 workspace 内的普通 skill package；runtime 只从当前 workspace snapshot 生成
`<available_skills>`。

本 ADR 不规定 materialization 的主体、命令或分发流程。Runtime 对 "materialized" 的可观察判定是：
workspace startup / explicit resource reload 生成 snapshot 时，存在解析后仍位于当前 workspace
`.magipi/skills/<skill-name>/SKILL.md` 的文件。

### Resource access

Host 在 resource reload 时只从当前 workspace `.magipi/skills/` 生成 skill resource snapshot。这个
snapshot 是 provider-visible skill list 和 skill grant 判断的唯一来源。

Snapshot 只在 workspace startup 或显式 resource reload 时刷新。Run 中新 materialize 的 skill 在下一次
reload 前不可见，也不能触发 env grant。

本 ADR 不引入 workspace 外 skill read / bash path 例外：

- pool 中的 skill 不能被 model 通过 ordinary `read` 直接发现或读取；
- workspace 外 skill 来源，包括 `neomagi/skill_pool/` 或历史/误建的 global `neomagi/magipi/skills/`，
  都不是 active resource root；
- supporting files 必须位于当前 workspace materialized skill package root 内；
- helper scripts 仍通过普通 `bash` 执行，且必须落在当前 workspace 允许的路径策略内；
- 这个规则不放宽 write/edit、redirects、destructive command、timeout、audit 和 redaction 策略。

### Skill env config

`resources.skillEnv.<skill>` 使用当前窄 schema：

```json
{
  "envFile": ".magipi/secrets/brave-search.env",
  "allow": ["BRAVE_API_KEY"]
}
```

`resources.skillEnv.<skill>` 是 workspace runtime 配置。相对 `envFile` 以 workspace root 为基准，
并且只有当同名 skill 已 materialized 到当前 workspace `.magipi/skills/` 时才参与 grant 判断。

当前阶段不定义 global `resources.skillEnv` runtime merge，也不做 project/global 字段级 deep merge。
如果以后要让 NeoMAGI 产品层管理 pool secret inventory，需要另写 ADR。

`disable-model-invocation: true` 的 skill 不进入 `<available_skills>`；即使 model 读到该 `SKILL.md`，
read-driven grant 也必须以 `disabled_model_invocation` diagnostic 跳过。

### Grant activation

Skill env grant 只能由 workspace-local 可观察行为激活。自然语言可以让 model 选择某个 skill，但
host 不因为自然语言里提到某个 skill、API key 或 provider 名称就提前注入 secret。

正常激活路径是 model 成功用普通 `read` 读取当前 workspace snapshot 中已发现 skill 的 `SKILL.md`。
Read-driven grant 在成功 `read SKILL.md` 的 tool result 被 host 处理后可用；它不会 retroactively
改变同一 dispatch batch 内已经提交的 tool calls。

Host 将同一个 assistant message 中的 tool calls 视为同一 dispatch batch。该 batch 内由
`read SKILL.md` 激活的 grant 只对后续 provider continuation 可见；同 batch 的 sibling `bash`
不接收这个刚激活的 grant。

`/skill:<name>` 仅作为 development/debug shortcut 保留：用户显式提交 `/skill:<name>`，且 host 成功
展开当前 workspace 中的 materialized skill 时，grant 可在本次 model run 第一个 tool call 前可用。

### Grant lifetime

Skill env grant 是单个 agent run / user submit 内 sticky 的运行态能力。这里的 run 指 host 接收一次
user submit 后，到 host 进入 idle 等待下一次 submit、abort 或 reset 为止；permission pause 仍属于同一 run。

- 当前 run idle、abort、reset 或进入下一次 user submit 时清除；
- 同一 run 内重复激活同一个 skill 是 idempotent；
- 同一 run 内尝试激活第二个不同 skill 的 env grant 产生 conflict diagnostic，不覆盖已有 grant；
- grant 只注入 model-originated `bash` tool call；
- 用户必须等待当前 run settle，或 abort/reset 当前 run 后，才能激活另一个 skill grant；
- 普通 user bash、extension-originated bash / command、read/write/edit/grep/find/ls 不接收 secret env。

### Secret handling

`bash` subprocess 不扩大全局 env allowlist。只有 active grant 的 `allow` 变量会通过 explicit
`extra_env` 注入。Audit、tool details、transcript 和 export 只记录 skill name、env var names、
source reference 与 diagnostic reason，不记录 secret value。若 helper 或 model 意外打印 secret，
tool output、rolling preview、full-output artifact 和 export 必须用 active grant 的 literal values 脱敏。
Redaction 不应对空值、过短值或常见词做全局盲替换；低质量 secret value 应产生 diagnostic 或采用不会
误伤普通 output 的脱敏策略。

缺失 `envFile`、缺失 allow var、disabled model invocation、grant conflict 都应产生 assistant-visible /
tool-details-visible 的非 secret diagnostic。

## 为什么

- 保持 Pi mono 的基本工具模型：skills 通过 `<available_skills>` progressive disclosure 进入 context，
  执行仍靠 atomic tools。
- 不允许 active global skills 可以把 provider-visible 能力限定在 workspace snapshot 内，避免 pool 或
  global secret 被隐式暴露。
- Skill pool 属于 NeoMAGI 产品层候选能力库；materialized 后才进入 workspace，runtime 不需要理解 pool
  的来源、审批、制作和分发流程。
- 0020 已经排除 active global skill secret 目录；本 ADR 进一步要求 secret 只在 workspace-local
  可观察行为激活后注入到 model-originated bash。
- `skillEnv` 不做 global/project 字段级 merge，可以避免 env file 与 allowlist 分属不同审计边界。
- Run-scoped sticky grant 让 skill setup check 后的后续 helper command 能拿到同一批 secret，同时不会跨用户请求残留。

## 放弃了什么

- 方案 A：新增 `read_skill` 或专用 skill runtime executor。
  - 放弃原因：会扩展基本 atomic tool 面，并形成第二套 skill execution 系统。
- 方案 B：把 `neomagi/skill_pool` 或 `neomagi/magipi/skills` 作为 provider-visible global skill root。
  - 放弃原因：workspace 外 capability 会直接进入 runtime，泄漏面和排障面都过大。
- 方案 C：自然语言匹配 skill description 时立即注入 secret。
  - 放弃原因：自然语言是 model 选择信号，不是 host 授权证据；必须看到 workspace-local
    `read SKILL.md`，或 development/debug `/skill` 展开。
- 方案 D：把 `BRAVE_API_KEY`、`GROQ_API_KEY` 等加入全局 bash env allowlist。
  - 放弃原因：所有 governed bash 都会拿到 secret，泄漏面过大。
- 方案 E：project `skillEnv` 与 global `skillEnv` 字段级 deep merge。
  - 放弃原因：secret source 与 allowlist 可能来自不同层，审计边界不清。
- 方案 F：session-wide 或 workspace-wide sticky grant。
  - 放弃原因：secret 会跨用户意图残留，违反最小作用域。

## 影响

- runtime skill discovery 只扫描当前 workspace `.magipi/skills/`；不扫描 `neomagi/skill_pool` 或
  `neomagi/magipi/skills`。
- system prompt 在 workspace startup / resource reload 后暴露 `<available_skills>` 轻量摘要，不包含
  skill body。
- path policy 不需要 workspace 外 global skill read/bash 例外。
- runtime 需要携带当前 active workspace skill、active package root 和 active `SkillEnvGrant`，并在 run settle 时清除。
- tool wrapper 只在 actor 是 model 且 tool 是 `bash` 时注入 active grant。
- `resources.skillEnv` 只为当前 workspace 中已 materialized 的同名 skill 生效。
- 实现锚点应覆盖 `cli.interactive.skill_env_grant`、skill prompt formatter、resource loader snapshot、
  model-originated bash tool wrapper 和 redaction/export 路径；后续重构必须保留本 ADR 的行为 contract。
- audit/redaction/export tests 应覆盖 secret value 不进入 transcript、audit value、full-output artifact 和 export。
- End-to-end tests 应覆盖 pool skill 不 provider-visible、workspace materialized skill 可见、
  `<available_skills>` 不含 body、read-driven activation、development/debug `/skill` activation、
  same-batch non-retroactivity、conflict、missing file、missing allow var、run settle clear，以及非
  materialized skill 不能触发 env grant。
