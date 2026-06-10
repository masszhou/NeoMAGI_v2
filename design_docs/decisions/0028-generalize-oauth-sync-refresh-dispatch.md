---
doc_id: 019eaf5b-35fe-73f9-9605-e37d16417c51
doc_id_format: uuidv7
doc_id_assigned_at: 2026-06-10T04:27:25+02:00
---
# 0028-generalize-oauth-sync-refresh-dispatch

- Status: accepted
- Date: 2026-06-10
- Related:
  - `dev_docs/plans/p3_extra_copilot_oauth_provider.md`
  - `packages/magipi/src/ai_provider/oauth.py`
  - `packages/magipi/src/ai_provider/oauth_github_copilot.py`
  - `packages/magipi/src/ai_provider/auth_storage.py`
  - `packages/magipi/src/ai_provider/credentials.py`
- Scope: 运行时取 key（同步路径）如何刷新已存的 OAuth 凭证，以及 OpenAI 兼容 adapter 如何解析 provider 的 base url。不改 P1 的 keyring/文件存储格式，不改 OpenAI Codex 流程语义。

## 选了什么

1. **同步刷新按 provider 派发，而非硬编码 openai-codex。**
   `auth_storage._resolve_entry_api_key` 原本写死 `provider != "openai-codex": return None`，且过期只调 `refresh_openai_oauth_credentials_sync`。改为一张 provider→刷新函数名的派发表 `_SYNC_OAUTH_REFRESHER_NAMES`，刷新函数在调用时经 `globals()` 解析（保留测试对模块级函数的 monkeypatch 能力）。每个刷新函数签名为 `(credentials, *, now_ms=...)`，`now_ms` 关键字必须保留。未登记的 OAuth provider 仍返回 `None`。

2. **新增内置 OAuth provider 用工厂表注册，保持依赖单向。**
   `oauth.py` 持有 `_BUILTIN_OAUTH_PROVIDER_FACTORIES`（默认含 OpenAI）和 `register_builtin_oauth_provider_factory(...)`。新 provider 模块（如 `oauth_github_copilot`）在 import 时把自己的工厂追加进去并立即注册，从而 `reset_oauth_providers_for_tests()` 也能重新注册。`oauth.py` **不** import 任何 provider 子模块，依赖方向恒为「子模块 → oauth」，无 import cycle。

3. **凭证解析与 base url 解析合并为单次契约。**
   新增 `auth_storage.resolve_stored_credential(...) -> StoredCredential{api_key, extra}`：一次解析（含刷新+写回副作用）同时给出 token 与 OAuth `extra` 元数据；`resolve_stored_api_key` 退化为它的薄封装。`credentials.resolve_provider_auth(model, options) -> ResolvedAuth{api_key, base_url}` 据此一次性给出 token 与 base url，`resolve_api_key` 退化为薄封装。base url 对一般 provider 即 `model.base_url`；对 GitHub Copilot 按「token 的 `proxy-ep` → 凭证 `extra` 里的 `enterpriseUrl`（`copilot-api.<domain>`）→ 个人版」顺序现算（与 pi-mono `getGitHubCopilotBaseUrl` 一致）。把 `extra` 一并带出，避免二次解析触发重复刷新，也保证企业 token 缺 `proxy-ep` 时仍回落到企业 host 而非个人版。

## 为什么

- 加第二个 OAuth provider（GitHub Copilot）时，硬编码 openai-codex 的同步路径会让 Copilot 凭证「存得下却取不出、过期不刷新」。派发表是最小且对称的解法。
- 用工厂表 + 子模块自注册，既能让新 provider 独立成文件（满足 800 行文件上限与关注点分离），又能避免 `oauth.py` 反向 import 子模块导致的循环依赖。
- Copilot 的 chat host 依 token 的 `proxy-ep` 而变（个人/business/enterprise 不同）。若 base url 独立于 api key 再解析一次，会二次触发 `resolve_stored_api_key` 的刷新副作用甚至竞争；合并成单次契约从根上消除该问题。

## 取舍与边界

- 刷新函数经 `globals()` 间接解析，是为了保住既有测试用 `monkeypatch.setattr(module, "refresh_*", ...)` 的注入方式；代价是静态分析看不到引用（用 `# noqa: F401` 标注 import 即可）。
- `resolve_provider_auth` 只在 Copilot 这一条 provider 上改写 base url；其余 provider 行为与改动前完全一致。
- 本 ADR 不覆盖 Claude-via-Copilot（anthropic-messages 走 Bearer/`auth_token` 的鉴权分支），那是后续 Phase F 的范围。
