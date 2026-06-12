---
doc_id: 019ebafd-4bfd-71f5-ba40-8f45747490eb
doc_id_format: uuidv7
doc_id_assigned_at: 2026-06-12T08:40:22+00:00
---
# 0029-package-shipped-system-skills

- Status: accepted
- Date: 2026-06-12
- Related: `design_docs/decisions/0020-magipi-workspace-and-global-resource-layout.md`
- Related: `design_docs/decisions/0021-workspace-materialized-skills-and-env-grants.md`
- Amends: `design_docs/decisions/0021-workspace-materialized-skills-and-env-grants.md` § 选了什么（materialization 留白处）

## 选了什么

magipi 随包发布一组 **system skills**（首批：`skill-creator`、`skill-installer`）。skill 源文件位于包内
`cli/resources/system_skills/<skill-name>/`，host 在每次 resource snapshot（workspace startup 或显式
`/reload`）时把它们 **host-managed materialize** 到当前 workspace 的
`.magipi/skills/.system/<skill-name>/`，随后按普通 workspace skill 参与发现。

- ADR-0021 的可观察契约不变：active runtime skill 仍然只来自当前 workspace `.magipi/skills/` 下的
  materialized 文件；system skills 之所以 active，是因为它们已经被 materialize 进 workspace。
- `.system/` 目录是 host 管理区：每次 snapshot 与包内源同步（新增/更新/删除随包走）；用户对该目录的
  手工修改会在下次 snapshot 被覆盖。想定制 system skill 的方式是把同名 skill 放进
  `.magipi/skills/<skill-name>/` —— 主 skill root 先扫描，collision keep-first，workspace 同名覆盖
  system 版本。
- 主 skill root 的目录遍历跳过点开头目录，因此 `.system/` 由一个**独立的低优先级 SkillSearchRoot**
  显式发现（`scope="system"`），不会重复发现。
- `resources.systemSkills: false`（workspace 或 global settings）关闭同步与发现；此时已存在的
  `.system/` 目录会在 snapshot 时被移除，避免留下 stale active skills。
- 同步失败（只读文件系统、权限不足等）产生 warning diagnostic，不阻塞启动；该次 snapshot 中
  system skills 以 `.system/` 现存内容为准（可能为空或 stale）。
- system skills 不预配 `resources.skillEnv`，不携带 secret；它们与普通 skill 一样经 governed atomic
  tools 执行，不获得任何 policy 例外。

## 为什么

- skill-creator / skill-installer 是产品自带能力，应当所有 workspace 开箱即用、包升级即更新，
  而不是每个 workspace 手工安装一份会过期的拷贝。
- post-P2 hardening 后 `read` / `grep` / `find` / `ls` 与 bash 输出路径都是严格 cwd-bound。
  把 system root 直接指向包安装目录需要给 hardened path policy 增加 workspace 外只读例外
  （`policy/path_policy.py`、`cli/tools/safe_file_ops.py`、`PolicyRequest` 协议、审计与回归面全要动）。
  host-managed materialization 把文件放进 cwd，安全边界零改动。
- ADR-0021 明确不规定 materialization 的主体、命令或分发流程；本 ADR 填这个空，而不是推翻
  "workspace materialized only" 决定。

## 放弃了什么

- 方案 A：包内目录作为 workspace 外 active skill root + path policy 只读例外。
  - 放弃原因：重新打开已硬化的 cwd containment 边界，扩大泄漏与排障面；收益只是省一次文件同步。
- 方案 B：`magipi skills bootstrap` 手工命令按 workspace 安装。
  - 放弃原因：每个 workspace 要手工装一次，拷贝随包升级过期；"开箱即用"不成立。
- 方案 C：把 system skill 全文注入 system prompt。
  - 放弃原因：违反 progressive disclosure，常驻 token 成本不可接受。

## 影响

- `cli/resources/system_skills.py`：包内源枚举 + workspace 同步（内容比对、增量替换、stale 清理、
  containment 断言、diagnostic 上报）。
- `cli/resources/loader.py`：snapshot 前执行同步；追加 `.magipi/skills/.system` 低优先级
  `SkillSearchRoot`。
- `cli/core/settings.py` / `cli/resources/settings.py`：新增 `resources.systemSkills`（默认 true）。
- 打包：hatchling wheel 默认包含 `cli/resources/system_skills/` 资源树（已通过 wheel 内容核实，
  无需 force-include 配置）。
- workspace 影响：在启用 system skills 的前提下，magipi 启动会在 cwd 创建
  `.magipi/skills/.system/`。建议 workspace `.gitignore` 忽略 `.magipi/skills/.system/`；
  skill-installer 的安装流程会给出该建议。
- 测试锚点：同步幂等、包升级更新、stale 清理、workspace 同名覆盖、opt-out 清理、只读 fs 降级、
  system skills 进入 `<available_skills>` 与 `/skill:` 展开。
