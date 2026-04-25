---
doc_id: 019d6457-9290-7ef5-be6b-40618a07a865
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-06T21:49:14+02:00
---
# Glossary

> 目的：为 NeoMAGI 提供轻量级、可持续维护的 Domain Ontology。  
> 口径优先级：`decisions/` > `design_docs/` > `dev_docs/`。  
> 范围：只收录跨文档反复出现、且容易混淆的核心术语；不追求枚举所有代码符号。

## 使用原则

- 术语冲突时，先以已接受 ADR 为准。
- 同一概念尽量只保留一个主词；历史别名只作为 `Aliases` 保留。
- 本文件解释“概念是什么”，不替代具体实现文档、计划或测试用例。

## 阅读导航

- `Identity & Prompt Context`：`SOUL`、`SOUL.md`、`USER.md`、`IDENTITY.md`、`Principal`

## Identity & Prompt Context

### SOUL
- **Category**：Identity / Governance Object
- **Aliases**：`soul`
- **Definition**：NeoMAGI 的受治理“自我/原则/价值观”对象。它回答“agent 是谁、按什么原则代表用户”，而不是“具体任务怎么做”。
- **Relations**：
  - `projected-as` → [SOUL.md](#soulmd)
  - `aligned-with` → [Principal](#principal)
  - `is-a` → [Growth Object](#growth-object)
  - `evaluated-by` → [GrowthEvalContract](#growthevalcontract)

### SOUL.md
- **Category**：Workspace Projection
- **Aliases**：`workspace/SOUL.md`
- **Definition**：当前 active `SOUL` 的运行时投影文件，不是最终真源。项目语义上以 DB 中 active soul version 为准，`SOUL.md` 负责工作区可见性和 prompt 注入。它回答“agent 是谁、按什么原则代表用户、采用什么内在人格/语气”，而不是“用户偏好是什么”或“外部展示名片是什么”。
- **Notes**：
  - post-bootstrap 常态下，`SOUL.md` 的变更必须走 `propose -> evaluate -> apply -> rollback` 治理路径；人类保留 veto/rollback 权限，但不直接改常态文本。
  - 在 workspace context 语义上，`SOUL.md` 的约束优先级低于 [USER.md](#usermd)，高于 [IDENTITY.md](#identitymd)。
- **Relations**：
  - `projection-of` → [SOUL](#soul)
  - `lower-priority-than` → [USER.md](#usermd)
  - `higher-priority-than` → [IDENTITY.md](#identitymd)

### USER.md
- **Category**：Workspace Context / User Preference
- **Aliases**：`workspace/USER.md`
- **Definition**：当前 workspace 中描述“这个 agent 正在为谁服务，以及应该如何个性化适配”的文件。它承载用户侧信息，如语言、时区、沟通风格、技术栈、长期偏好和不希望被违背的回答习惯，回答“服务对象是谁、应该怎样配合这个用户”，而不是“agent 自己是谁”或“agent 对外叫什么名字”。
- **Notes**：
  - 当前设计中，`USER.md` 是每 turn 直接注入的 workspace context 文件，不是 DB-backed projection，也不是当前 growth governance 的对象。
  - 语义上它属于“用户侧约束 / 个性化层”，冲突时优先级高于 [SOUL.md](#soulmd) 和 [IDENTITY.md](#identitymd)。
  - `USER.md` 不等同于 `principal`、认证后的 canonical user identity、`account_id` 或 `peer_id`；这些身份绑定语义属于 `P2-M3` 的 principal / binding 模型，而不是当前的偏好文件。
- **Relations**：
  - `customizes` → [Principal](#principal)
  - `higher-priority-than` → [SOUL.md](#soulmd)
  - `higher-priority-than` → [IDENTITY.md](#identitymd)

### IDENTITY.md
- **Category**：Workspace Context / Presentation
- **Aliases**：`workspace/IDENTITY.md`
- **Definition**：当前 workspace 中描述 agent 外在身份名片的结构化文件，用于展示层和表面呈现，例如 name、role、voice 或类似 metadata。它回答“外界看到你叫什么、你以什么角色出现”，而不是“你的内在原则是什么”或“当前服务的用户偏好是什么”。
- **Notes**：
  - `IDENTITY.md` 是展示层语义，不是 `SOUL` 的治理对象，不承载 `propose/evaluate/apply/rollback` 生命周期。
  - 它也不等同于 `principal`、认证身份、`account_id`、`peer_id` 或渠道绑定证据；那些是运行时 identity / binding 语义，不是展示名片。
  - 在 workspace context 语义上，`IDENTITY.md` 优先级低于 [USER.md](#usermd) 和 [SOUL.md](#soulmd)。
- **Relations**：
  - `presentation-layer-for` → [SOUL](#soul)
  - `lower-priority-than` → [USER.md](#usermd)
  - `lower-priority-than` → [SOUL.md](#soulmd)
  - `not-the-same-as` → [Principal](#principal)

### Principal
- **Category**：Identity / Runtime
- **Aliases**：user-interest line
- **Definition**：NeoMAGI 在运行时所代表的“同一个用户利益”身份轴。多 agent 默认共享同一个 `SOUL / principal`，而不是各自拥有独立长期人格。
- **Notes**：
  - `principal` 是运行时概念，不是 workspace 文件；它承载“为谁服务、代表什么利益”的运行时语义，不负责展示名片或工作区个性化。
  - NeoMAGI agent 有能力和主用户之外的其他人进行交互
  - 如果只有 SOUL，系统仍然回答不了“WebChat 的这个登录用户”和“Telegram 的那个 peer”是不是同一个人。
  - 记忆检索和写入要先按 principal_id 和 visibility 过滤，否则会把“agent 的人格”误当成“用户身份边界”，这在 shared-space / cross-principal 场景下不安全。
- **Relations**：
  - `co-defined-by` → [SOUL](#soul)