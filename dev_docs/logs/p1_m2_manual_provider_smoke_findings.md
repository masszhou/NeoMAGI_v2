---
doc_id: 019dd068-4c1b-740e-afe1-7ab86bd9b8be
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-27T21:26:22+02:00
---
# P1-M2 Manual Provider Smoke Findings

- Status: done
- Date: 2026-04-27
- Scope: `dev_docs/user_tests/p1_m2_manual_test_plan.md`
- Plan: `dev_docs/plans/p1_m2_pi_ai_core.md`

## 总结

P1-M2 provider core 手动测试已完成并通过。手测覆盖：

- Anthropic env API key streaming；
- Anthropic prompt cache payload / response / usage；
- OpenAI env API key streaming；
- OpenAI Responses prompt cache；
- OpenAI Responses tool call streaming；
- OpenAI OAuth provider 离线测试与真实登录；
- OpenAI Codex OAuth 本地 auth storage 复用；
- `openai-codex-responses` 真实 streaming；
- `openai-codex-responses` prompt cache payload / header / usage。

本轮手测发现并修复了 5 个真实问题，另补齐 OpenAI Codex OAuth adapter、auth storage、
Codex prompt cache 手测说明和离线回归。

## 修复 1：Anthropic 内建 model id 过期

原内建模型：

```text
claude-3-5-haiku-20241022
```

真实 Anthropic API 返回 `not_found_error`。已改为当前 Claude API ID：

```text
claude-haiku-4-5-20251001
```

同步更新：

- `src/ai_provider/model_registry.py` 内建 Anthropic model；
- Anthropic 成本、context、max output、reasoning capability；
- Anthropic fixtures、usage tests、cross-provider handoff tests；
- 手动测试说明中的默认 Anthropic model。

验收结果：Anthropic env smoke 输出 `ANTHROPIC_OK`，`STOP stop`，usage 非零。

## 修复 2：Anthropic SDK stream 递归错误

真实 Anthropic stream 曾返回：

```text
maximum recursion depth exceeded
```

根因是 `iterate_provider_stream()` 优先处理 `__aenter__`。Anthropic SDK enter 后的
stream 对象同时实现 `__aenter__` 和 `__aiter__`，且 `__aenter__` 返回自身，导致递归。

修复：

- async iterable 优先按 `__aiter__` 消费；
- 只有纯 context manager 才先 enter 再消费。

新增回归：

- async iterable + context manager 双实现对象不递归；
- pure manager 只 enter 一次。

## 修复 3：OpenAI Responses tool call alias 解析

真实 OpenAI Responses tool call smoke 曾出现同一个工具调用被拆成两个 Pi `ToolCall`：

```text
CONTENT [
  {'type': 'toolCall', 'id': 'call_...', 'name': 'read', 'arguments': {'path': 'README.md'}},
  {'type': 'toolCall', 'id': 'fc_...', 'name': '', 'arguments': {}}
]
```

根因：

- `response.output_item.added` 同时带 `id=fc_...` 和 `call_id=call_...`；
- 后续 argument delta 真实流可能只带 `item_id=fc_...`；
- 旧 parser 只按 `call_id` 建索引，导致 delta 误创建第二个空 tool call。

修复：

- `id` 和 `call_id` 均登记为同一 content index 的 alias；
- argument delta 按 alias 找回同一 tool call；
- `toolcall_end` 只发送一次。

验收结果：真实 3.1 smoke 只保留一个 `read` tool call，`STOP toolUse`。

## 新增 1：OpenAI Codex Responses adapter

OpenAI OAuth 登录拿到的是 Codex / ChatGPT access token，不能直接作为 standard
`OPENAI_API_KEY` 交给 direct `openai-responses`。本轮新增最小
`openai-codex-responses` adapter：

- API family：`openai-codex-responses`；
- model：`openai-codex/gpt-5.3-codex`；
- endpoint：`https://chatgpt.com/backend-api/codex/responses`；
- headers：`Authorization`、`chatgpt-account-id`、`originator: pi`、
  `OpenAI-Beta: responses=experimental`；
- request body：Responses-style streaming payload，包含
  `include=["reasoning.encrypted_content"]`；
- event parser：Codex `response.done` / `response.completed` / `response.incomplete`
  归一到 Responses parser。

真实 5.3 smoke 结果：

```text
CODEX_OAUTH_OK
EVENTS ['start', 'text_start', 'text_delta', ..., 'text_end', 'done']
STOP stop
TEXT CODEX_OAUTH_OK
USAGE totalTokens=40
```

判定：OpenAI OAuth-backed Codex streaming pass。

## 新增 2：OpenAI Codex OAuth 本地 auth storage

为避免每次真实 smoke 都重新 OAuth，本轮新增本地 credential storage：

- 默认路径：`~/.neomagi/auth.json`；
- 可用 `NEOMAGI_AUTH_PATH` 覆盖；
- `openai-codex` entry 兼容 Pi agent 形态：
  `type/access/refresh/expires/accountId`；
- auth 文件权限收敛为 `0600`，父目录为 `0700`；
- `openai-codex` provider 无显式 `StreamOptions.api_key` 时优先读取本地 OAuth；
- access token 临近过期时用 refresh token 刷新并写回；
- `OPENAI_CODEX_OAUTH_TOKEN` 保留为临时 env fallback。

新增离线测试：

- OAuth credential 落盘形态和权限；
- fresh credential 直接复用 access token；
- expired credential refresh 后写回；
- `openai-codex-responses` 不传 `api_key` 时从 auth storage 取 token。

## 修复 4：Codex request 缺少 instructions / input shape 不一致

真实 5.3 首次运行返回：

```text
OpenAI Codex endpoint returned HTTP 400: {"detail":"Instructions are required"}
```

交叉核对 pi-mono 后确认：

- Codex request 必须把 system prompt 放在顶层 `instructions`；
- system prompt 不放入 `input`；
- user text 使用 Responses-format content array：
  `{"role": "user", "content": [{"type": "input_text", "text": "..."}]}`。

修复：

- 手测 5.3 强制设置 `Context.systemPrompt`；
- `openai-codex-responses` 使用 Codex 专用 message conversion；
- transport 前增加 preflight，缺少非空 `instructions` 时本地报错，不先打真实 backend。

## 新增 3：OpenAI Codex prompt cache 手测

Codex cache 与 direct OpenAI Responses 不同：

- 需要 `cache_retention != "none"`；
- 需要稳定 `session_id`；
- adapter 发送 `prompt_cache_key`、`session_id`、`x-client-request-id`；
- adapter 不发送 `prompt_cache_retention`；
- `short` / `long` 当前只表示启用 cache key，不代表 TTL 字段；
- cache read 成功以 usage `cacheRead > 0` 判定；
- `cacheWrite` 期望保持 0。

真实 5.4.2 smoke 结果：

```text
PAYLOAD_CACHE_EVIDENCE prompt_cache_key=manual-codex-cache-001
REQUEST_HEADER_EVIDENCE session_id=manual-codex-cache-001
RUN 1 STOP stop USAGE input=12033 cacheRead=0 cacheWrite=0 totalTokens=12041
RUN 2 STOP stop USAGE input=129 cacheRead=11904 cacheWrite=0 totalTokens=12041
RUN 3 STOP stop USAGE input=129 cacheRead=11904 cacheWrite=0 totalTokens=12041
```

判定：Codex OAuth path + `prompt_cache_key` + session headers + `cacheRead` usage
全部成功。成本从首轮约 `0.02117` 降到后续约 `0.00242`，与 cache read 命中一致。

## 手动测试最终判定

以下当前 P1-M2 手测项均通过：

- Anthropic env key streaming；
- Anthropic prompt cache read；
- OpenAI env key streaming；
- OpenAI Responses prompt cache read；
- OpenAI Responses tool call streaming；
- OpenAI OAuth provider 离线测试；
- OpenAI OAuth 真实登录并落盘；
- OpenAI Codex OAuth-backed streaming；
- OpenAI Codex prompt cache request contract；
- OpenAI Codex prompt cache真实 read；
- OpenAI Codex cache disabled negative smoke；
- TUI `/login` / `/logout` stub 只提示未实现，不假装成功。

以下不属于当前 P1-M2 手动测试 pass/fail：

- 真实 TUI `/login` / `/logout`；
- TUI credential source UI；
- TUI 内 prompt cache 设置和 usage 展示；
- M1 已覆盖的 TUI lifecycle / playback / editor / terminal restore。

## 自动化验证

最终通过：

```text
uv run pytest tests/ -q
just lint
git diff --check
```

结果：

- full `tests/`：298 passed；
- `just lint`：ruff passed，`complexity_guard regressions=0`；
- `git diff --check` clean。

## 结论

P1-M2 `pi-ai` provider core 已完成真实 provider 手动 sign-off。M2 范围内的
Anthropic / OpenAI / OpenAI Codex provider runtime、OAuth credential 复用、prompt cache
payload/usage normalization、tool call streaming 均已通过当前手动和自动化验收。
