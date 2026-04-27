---
doc_id: 019dcd72-26db-7704-85cf-b166f1f2bb51
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-27T07:38:14+02:00
---
# P1-M2 用户手动测试说明书：真实 Provider / 登录 / Prompt Cache

- Status: draft
- Date: 2026-04-27
- Target: P1-M2 `pi-ai` provider core（commits `ed1335b`、`33d8d9e`）
- Plan: `dev_docs/plans/p1_m2_pi_ai_core.md`
- Reference: `dev_docs/user_tests/p1_m1_manual_test_plan.md`
- 适用平台：macOS Terminal / iTerm2 / Ubuntu 常见终端
- 涉及真实付费 API：OpenAI、Anthropic

> **当前实现边界**：M2 已支持真实 provider runtime、环境变量 API key、OpenAI-only
> OAuth provider core、本地 auth storage、prompt cache payload/usage normalization；
> 但 TUI 仍是 M1 mock shell，尚未接真实 agent runtime。`/login`、`/logout`
> 目前只是 slash command stub，尚未接 OAuth provider。
>
> 因此本文分两类：
> - **当前可执行**：用 Python provider runtime 做真实 API smoke、prompt cache 验证。
> - **OpenAI OAuth core**：当前可用离线单测和可选真实登录 smoke 验证；
>   OpenAI Codex OAuth 可落盘到本地 auth storage 后复用。
> - **TUI 登录**：当前只验证 `/login`、`/logout` stub 不误导用户；真实 TUI auth
>   flow 不属于本说明书的当前手动测试范围。

> **CLI 调用约定**：开发/测试期一律使用 `uv run python -m cli ...`；不要在 dev
> 文档里依赖 `neomagi` shim。

---

## 0. 准备与安全

### 0.1 基础环境

```bash
uv sync
uv run pytest tests/ -q
just lint
uv run python -m cli --help
```

**期望**：
- pytest / lint 全绿；
- `--help` 可显示 `--playback`、`--print`、`--help`；
- 如果基础门禁不通过，先不要跑真实 API。

### 0.2 真实 API 安全约束

- 不要把 API key 写入任何 repo 文件。
- 推荐在一次性 shell session 里 `export`，测试后 `unset`。
- 真实 API smoke 只用短输出 prompt；prompt cache 测试会用较长 prefix，但仍要控制轮数。
- 如果 provider dashboard 有 budget / project limit，先开 limit。
- 记录结果时只记录 key 来源、model、usage，不记录 secret、完整 OAuth token、完整请求 header。

### 0.3 测试结果记录表

每个 case 建议记录：

| Case | Provider | Credential source | Model | Cache retention | sessionId | Result | Usage evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | anthropic | env `ANTHROPIC_API_KEY` | `claude-haiku-4-5-20251001` | none | n/a | pass/fail | `cacheRead/cacheWrite` |
| O1 | openai | env `OPENAI_API_KEY` | `gpt-4o-mini` | none | n/a | pass/fail | `cacheRead/cacheWrite` |

---

## 1. 当前可执行：环境变量 API Key 登录路径

M2 的 credential 顺序是：

1. `StreamOptions.api_key` 显式传入；
2. `openai-codex` 优先读取本地 auth storage（默认 `~/.neomagi/auth.json`，可用
   `NEOMAGI_AUTH_PATH` 覆盖）；
3. provider 环境变量；
4. 其他 provider 可在无 env 时读取本地 auth storage 中的 `api_key` entry；
5. 否则报错。

当前 provider 环境变量名：

- Anthropic: `ANTHROPIC_API_KEY`
- OpenAI: `OPENAI_API_KEY`
- OpenAI Codex OAuth token fallback: `OPENAI_CODEX_OAUTH_TOKEN`

### 1.1 缺失 key 的负向检查

```bash
unset ANTHROPIC_API_KEY
unset OPENAI_API_KEY

uv run python - <<'PY'
from ai_provider.credentials import resolve_api_key
from ai_provider.model_registry import get_model

for provider, model_id in [
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("openai", "gpt-4o-mini"),
]:
    try:
        resolve_api_key(get_model(provider, model_id))
    except RuntimeError as exc:
        print(provider, "OK", str(exc))
    else:
        raise SystemExit(f"{provider}: expected missing-key error")
PY
```

**期望**：两个 provider 都打印 `missing API key` 类错误；不得静默 fallback 到其他
credential。

### 1.2 Anthropic env API key 基本连通

```bash
export ANTHROPIC_API_KEY='sk-ant-...'

uv run python - <<'PY'
import asyncio

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage


async def main() -> None:
    model = get_model("anthropic", "claude-haiku-4-5-20251001")
    context = Context(
        systemPrompt="Answer in one short sentence.",
        messages=[UserMessage(content="Say exactly: ANTHROPIC_OK", timestamp=1)],
    )
    result_stream = stream(model, context, StreamOptions(cache_retention="none"))
    seen = []
    async for event in result_stream:
        seen.append(event.type)
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)
        elif event.type == "error":
            print("\nERROR", event.error.error_message)
    result = await result_stream.result()
    print("\nEVENTS", seen)
    print("STOP", result.stop_reason)
    if result.error_message:
        print("ERROR_RESULT", result.error_message)
    print("USAGE", result.usage.model_dump(by_alias=True))


asyncio.run(main())
PY
```

**期望**：
- 输出包含 `ANTHROPIC_OK` 或含义等价的短答；
- `EVENTS` 至少包含 `start`、`text_delta`、`done`；
- `STOP stop`；
- `USAGE.totalTokens > 0`。

### 1.3 OpenAI env API key 基本连通

```bash
export OPENAI_API_KEY='sk-...'

uv run python - <<'PY'
import asyncio

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage


async def main() -> None:
    model = get_model("openai", "gpt-4o-mini")
    context = Context(
        systemPrompt="Answer in one short sentence.",
        messages=[UserMessage(content="Say exactly: OPENAI_OK", timestamp=1)],
    )
    result_stream = stream(model, context, StreamOptions(cache_retention="none"))
    seen = []
    async for event in result_stream:
        seen.append(event.type)
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)
        elif event.type == "error":
            print("\nERROR", event.error.error_message)
    result = await result_stream.result()
    print("\nEVENTS", seen)
    print("STOP", result.stop_reason)
    if result.error_message:
        print("ERROR_RESULT", result.error_message)
    print("USAGE", result.usage.model_dump(by_alias=True))


asyncio.run(main())
PY
```

**期望**：
- 输出包含 `OPENAI_OK` 或含义等价的短答；
- `EVENTS` 至少包含 `start`、`text_delta`、`done`；
- `STOP stop`；
- `USAGE.totalTokens > 0`。

### 1.4 显式 `StreamOptions.api_key` 优先级

只在你有一把单独测试 key 时执行。不要把 key 写入 shell history；可以用临时环境变量承载。

```bash
export OPENAI_API_KEY='invalid-or-empty-test-value'
export NEOMAGI_MANUAL_OPENAI_KEY='sk-valid-test-key'

uv run python - <<'PY'
import asyncio
import os

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage


async def main() -> None:
    model = get_model("openai", "gpt-4o-mini")
    context = Context(messages=[UserMessage(content="Say OPENAI_OVERRIDE_OK", timestamp=1)])
    result = await stream(
        model,
        context,
        StreamOptions(api_key=os.environ["NEOMAGI_MANUAL_OPENAI_KEY"], cache_retention="none"),
    ).result()
    print(result.stop_reason, result.usage.model_dump(by_alias=True))


asyncio.run(main())
PY
```

**期望**：请求成功，证明 explicit option 覆盖 env key。测完：

```bash
unset NEOMAGI_MANUAL_OPENAI_KEY
```

---

## 2. 当前可执行：Prompt Cache 真实 API 验证

判断 prompt cache 成功不能只看请求字段。需要两类证据：

1. **payload 证据**：NeoMAGI 确实发送了 provider 要求的 cache 字段；
2. **usage 证据**：provider 返回的 usage 中 `cacheRead` 或 `cacheWrite` 非零；
3. **response 证据**：assistant 内容、`STOP stop`、非零 usage，证明真实 provider
   回复完成，而不是只完成 payload hook。

如果 usage 仍为 0，但 payload 正确，先把 prefix 加长、重复第二次请求、检查 provider
dashboard。cache miss 是正常 provider 行为，不等价于 NeoMAGI bug。

### 2.1 Anthropic prompt cache：payload + response + usage

Anthropic M2 策略：

- 给 system / last tool / last user block 打 `cache_control`；
- direct `api.anthropic.com` + `cache_retention="long"` 时使用 `ttl="1h"`；
- `session_id` 不参与 Anthropic cache affinity。

```bash
export ANTHROPIC_API_KEY='sk-ant-...'

uv run python - <<'PY'
import asyncio
import json

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage

LONG_PREFIX = "Manual Anthropic prompt cache stable prefix. " * 900


def on_payload(payload, model):
    print("PAYLOAD_CACHE_EVIDENCE", json.dumps({
        "model": payload.get("model"),
        "system_cache_control": payload.get("system", [{}])[0].get("cache_control"),
        "last_user_cache_control": payload["messages"][-1]["content"][-1].get("cache_control"),
    }, ensure_ascii=False))


def assistant_text(result):
    return "".join(block.text for block in result.content if block.type == "text")


async def one_call(i: int):
    model = get_model("anthropic", "claude-haiku-4-5-20251001")
    context = Context(
        systemPrompt=f"You are testing provider prompt cache. Stable prefix:\n{LONG_PREFIX}",
        messages=[UserMessage(content=f"Run {i}: answer with CACHE_OK.", timestamp=i)],
    )
    result = await stream(
        model,
        context,
        StreamOptions(cache_retention="long", on_payload=on_payload),
    ).result()
    usage = result.usage.model_dump(by_alias=True)
    print("RUN", i, "TEXT", assistant_text(result))
    print("RUN", i, "STOP", result.stop_reason, "USAGE", usage)
    return usage


async def main() -> None:
    first = await one_call(1)
    second = await one_call(2)
    if first["cacheWrite"] == 0 and second["cacheRead"] == 0:
        print("WARN cache usage was zero; retry with longer prefix or inspect provider dashboard")


asyncio.run(main())
PY
```

**期望**：
- `PAYLOAD_CACHE_EVIDENCE.system_cache_control` 和 `last_user_cache_control` 为
  `{"type": "ephemeral", "ttl": "1h"}`；
- 每轮 `TEXT` 包含 `CACHE_OK` 或含义等价的短答，`STOP stop`，`USAGE.totalTokens > 0`；
- `cacheWrite > 0` 证明 provider 接受并写入 cache；第二轮 `cacheRead > 0` 才证明命中
  cache read；
- 如果两轮都是 `cacheWrite > 0` 且 `cacheRead == 0`，说明写入成功但未命中 read。常见原因是
  cache breakpoint 包含了每轮变化的 user block（例如 `Run 1` / `Run 2`），provider 需要在
  相同 prefix 的 cache breakpoint 上找到上一轮写入才能返回 read；
- 如果 usage 不稳定，记录 provider dashboard 截图/日志，不把 cache miss 当 hard fail。

### 2.2 Anthropic cache disabled

```bash
export ANTHROPIC_API_KEY='sk-ant-...'

uv run python - <<'PY'
import asyncio

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage


def on_payload(payload, model):
    text = str(payload)
    forbidden = ["cache_control", "prompt_cache_key", "prompt_cache_retention", "session_id"]
    leaked = [key for key in forbidden if key in text]
    print("LEAKED", leaked)
    if leaked:
        raise RuntimeError(f"cache fields leaked: {leaked}")


async def main() -> None:
    model = get_model("anthropic", "claude-haiku-4-5-20251001")
    context = Context(messages=[UserMessage(content="cache disabled smoke", timestamp=1)])
    result = await stream(
        model,
        context,
        StreamOptions(cache_retention="none", on_payload=on_payload),
    ).result()
    print(result.stop_reason, result.usage.model_dump(by_alias=True))


asyncio.run(main())
PY
```

**期望**：`LEAKED []`；usage 中 `cacheRead == 0` 且 `cacheWrite == 0`。

### 2.3 OpenAI Responses prompt cache：payload + usage

OpenAI M2 默认 direct path 是 Responses API：

- cache enabled + `session_id` 时发送 `prompt_cache_key=session_id`；
- direct `api.openai.com` + long retention 时发送 `prompt_cache_retention="24h"`；
- usage 中 `input_tokens_details.cached_tokens` 归一化为 `Usage.cacheRead`；
- OpenAI Responses 没有 cache write token，`cacheWrite` 期望为 0。

```bash
export OPENAI_API_KEY='sk-...'

uv run python - <<'PY'
import asyncio
import json

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage

SESSION_ID = "manual-openai-cache-001"
LONG_PREFIX = "Manual OpenAI prompt cache stable prefix. " * 900


def on_payload(payload, model):
    print("PAYLOAD_CACHE_EVIDENCE", json.dumps({
        "model": payload.get("model"),
        "prompt_cache_key": payload.get("prompt_cache_key"),
        "prompt_cache_retention": payload.get("prompt_cache_retention"),
        "has_cache_control": "cache_control" in str(payload),
    }, ensure_ascii=False))


async def one_call(i: int):
    model = get_model("openai", "gpt-4o-mini")
    context = Context(
        systemPrompt=f"You are testing provider prompt cache. Stable prefix:\n{LONG_PREFIX}",
        messages=[UserMessage(content=f"Run {i}: answer with CACHE_OK.", timestamp=i)],
    )
    result = await stream(
        model,
        context,
        StreamOptions(cache_retention="long", session_id=SESSION_ID, on_payload=on_payload),
    ).result()
    usage = result.usage.model_dump(by_alias=True)
    print("RUN", i, "STOP", result.stop_reason, "USAGE", usage)
    return usage


async def main() -> None:
    first = await one_call(1)
    second = await one_call(2)
    if second["cacheRead"] == 0:
        print("WARN cacheRead was zero; retry with longer prefix or inspect provider dashboard")
    if first["cacheWrite"] != 0 or second["cacheWrite"] != 0:
        raise RuntimeError("OpenAI Responses cacheWrite should remain 0")


asyncio.run(main())
PY
```

**期望**：
- `prompt_cache_key == "manual-openai-cache-001"`；
- `prompt_cache_retention == "24h"`；
- `has_cache_control == false`；
- 第二轮常见 `cacheRead > 0`；
- `cacheWrite == 0`。

### 2.4 OpenAI cache disabled

```bash
export OPENAI_API_KEY='sk-...'

uv run python - <<'PY'
import asyncio

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage


def on_payload(payload, model):
    forbidden = ["prompt_cache_key", "prompt_cache_retention", "cache_control"]
    leaked = [key for key in forbidden if key in str(payload)]
    print("LEAKED", leaked)
    if leaked:
        raise RuntimeError(f"cache fields leaked: {leaked}")


async def main() -> None:
    model = get_model("openai", "gpt-4o-mini")
    context = Context(messages=[UserMessage(content="cache disabled smoke", timestamp=1)])
    result = await stream(
        model,
        context,
        StreamOptions(cache_retention="none", session_id="manual-openai-cache-off", on_payload=on_payload),
    ).result()
    print(result.stop_reason, result.usage.model_dump(by_alias=True))


asyncio.run(main())
PY
```

**期望**：`LEAKED []`；usage 中 `cacheRead == 0` 且 `cacheWrite == 0`。

### 2.5 OpenAI Chat Completions prompt cache（可选）

如果要覆盖 Chat Completions direct path，把 §2.3 的 model 改为：

```python
model = get_model("openai", "gpt-4o-mini-chat-completions")
```

**期望**：
- direct OpenAI payload 同样发送 `prompt_cache_key`；
- long retention 发送 `prompt_cache_retention="24h"`；
- usage 中 `cacheRead` 来自 cached tokens；
- 如果 provider 返回 `cache_write_tokens`，NeoMAGI 将其归一化为 `cacheWrite`。

---

## 3. 当前可执行：Provider 行为 smoke

### 3.1 Tool call streaming（真实 provider）

这个 case 验证真实 provider 能把 tool call 解析成 Pi-compatible `toolcall_*` events。
模型有时会拒绝调用工具；如果一次没有 tool call，最多重试 3 次。

```bash
export OPENAI_API_KEY='sk-...'

uv run python - <<'PY'
import asyncio

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, Tool, UserMessage


async def main() -> None:
    model = get_model("openai", "gpt-4o-mini")
    context = Context(
        systemPrompt="When a read tool is available, call it instead of answering directly.",
        messages=[UserMessage(content="Use the read tool with path README.md.", timestamp=1)],
        tools=[
            Tool(
                name="read",
                description="Read a file by path.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            )
        ],
    )
    result_stream = stream(model, context, StreamOptions(cache_retention="none"))
    seen = []
    async for event in result_stream:
        seen.append(event.type)
        print(event.type)
    result = await result_stream.result()
    print("STOP", result.stop_reason)
    print("CONTENT", [block.model_dump(by_alias=True) for block in result.content])


asyncio.run(main())
PY
```

**期望**：
- 成功路径包含 `toolcall_start`、`toolcall_delta`、`toolcall_end`、`done`；
- final message 含 `{"type": "toolCall", "name": "read", "arguments": {"path": "README.md"}}`；
- 如果 3 次都没有 tool call，记录为 provider/model behavior issue，而不是 transport crash。

## 4. 当前 TUI `/login` stub 检查

当前 `/login`、`/logout` 已在 slash command 表里注册，但只是 M9 stub。

```bash
uv run python -m cli
```

在 TUI 内依次输入：

```text
/login
/logout
```

**当前期望**：
- autocomplete 能看到 `/login`、`/logout`；
- 执行后出现类似 `/login not implemented in M1; tracked in M9 (OAuth login)` 的提示；
- 不应打开假的 auth flow；
- 不应写任何 credential 文件；
- 不应声称已经登录。

这不是最终验收，只是防止当前 mock TUI 误导用户。

---

## 5. OpenAI OAuth / Codex Provider

P1 core OAuth scope：

- **OpenAI**：实现并测试 OpenAI OAuth provider core；OAuth access token 可直接驱动
  `openai-codex-responses` provider；provider runtime 可读写本地 auth storage。
- **Anthropic**：P1 core 不承诺、不实现 OAuth/subscription login；只承诺
  `ANTHROPIC_API_KEY` env key 路径。

### 5.1 当前可执行：OpenAI OAuth provider 离线测试

```bash
uv run pytest tests/ai_provider/test_openai_oauth.py -q
```

**期望**：
- 内建 OAuth registry 只有 `openai`，`anthropic` 查找失败；
- OpenAI authorize URL 包含 PKCE challenge、state、`codex_cli_simplified_flow=true`；
- manual redirect URL / auth code 可解析，并校验 state；
- token exchange 和 refresh 都走可 mock 的 token endpoint；
- 过期 credential 会刷新，未过期 credential 不刷新。

### 5.2 当前可执行：OpenAI OAuth provider 真实登录 + 落盘 smoke（可选）

本 smoke 验证 OAuth flow 能拿到 OpenAI/Codex credential，并写入本地 auth storage。
默认文件为 `~/.neomagi/auth.json`；如需隔离测试，可先设置：

```bash
export NEOMAGI_AUTH_PATH="$HOME/.neomagi/auth.manual-test.json"
```

执行前确保没有其他进程占用 `127.0.0.1:1455`。脚本不会打印完整 token。

```bash
unset OPENAI_API_KEY
uv run python - <<'PY'
import asyncio
from ai_provider.auth_storage import resolve_auth_path, save_oauth_credentials
from ai_provider.oauth import OAuthLoginCallbacks, get_oauth_provider

async def main() -> None:
    provider = get_oauth_provider("openai")

    def on_auth(info):
        print("Open this URL in a browser:")
        print(info.url)

    def on_prompt(prompt):
        return input(prompt.message + " ")

    creds = await provider.login(
        OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)
    )
    save_oauth_credentials("openai-codex", creds)
    print(
        {
            "authPath": str(resolve_auth_path()),
            "accountId": creds.account_id,
            "expires": creds.expires,
            "accessTokenLength": len(creds.access),
            "refreshTokenLength": len(creds.refresh),
        }
    )

asyncio.run(main())
PY
```

**期望**：
- 浏览器完成登录后跳回 `http://localhost:1455/auth/callback`；
- 脚本输出 `accountId`、`expires` 和 token 长度；
- 不输出完整 access token / refresh token；
- repo 内没有新增 secret 文件；
- `authPath` 指向的本地文件存在，权限应为 owner-only（macOS/Ubuntu 上通常是
  `0600`），内容结构类似：

```json
{
  "openai-codex": {
    "type": "oauth",
    "access": "...",
    "refresh": "...",
    "expires": 1777845414783,
    "accountId": "..."
  }
}
```

### 5.3 当前可执行：OAuth-backed Codex stream smoke（可选）

本 smoke 验证 5.2 拿到的 Codex OAuth access token 能实际驱动
`openai-codex-responses` adapter。它走 `https://chatgpt.com/backend-api/codex/responses`，
不是 direct OpenAI API key 路径。它不再重新登录，也不显式传 `api_key`；adapter
应自动从本地 auth storage 读取 OAuth access token，过期时用 refresh token 刷新并写回。

OpenAI Codex backend 要求顶层 `instructions` 字段。Pi 的 coding-agent 总是把
system prompt 作为 `instructions` 发送，用户文本只放在 Responses-format `input`
里；因此本 smoke 必须设置 `Context.systemPrompt`。

```bash
unset OPENAI_API_KEY
uv run python - <<'PY'
import asyncio

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage


def text(result):
    return "".join(block.text for block in result.content if block.type == "text")


async def main() -> None:
    model = get_model("openai-codex", "gpt-5.3-codex")
    context = Context(
        systemPrompt="You are a concise coding assistant. Follow the user's exact output instruction.",
        messages=[UserMessage(content="Say exactly CODEX_OAUTH_OK", timestamp=1)],
    )
    result_stream = stream(model, context, StreamOptions(cache_retention="none"))
    seen = []
    async for event in result_stream:
        seen.append(event.type)
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)
        elif event.type == "error":
            print("\nERROR", event.error.error_message)
    result = await result_stream.result()
    print("\nEVENTS", seen)
    print("STOP", result.stop_reason)
    if result.error_message:
        print("ERROR_RESULT", result.error_message)
    print("TEXT", text(result))
    print("USAGE", result.usage.model_dump(by_alias=True))


asyncio.run(main())
PY
```

**期望**：
- 先完成 5.2，或者 `NEOMAGI_AUTH_PATH` 指向已存在的 OAuth auth storage；
- adapter 从 OAuth access token 解析 `chatgpt_account_id` 并发送 Codex backend 请求；
- 输出包含 `CODEX_OAUTH_OK` 或含义等价的短答；
- `EVENTS` 至少包含 `start`、`text_delta`、`done`；
- `STOP stop`，`USAGE.totalTokens > 0`；
- 不输出完整 token；如果 access token 过期，auth storage 中的 refresh 后 credential
  会被写回。

### 5.4 当前可执行：OpenAI Codex Responses prompt cache（OAuth，可选）

这个 case 覆盖 `openai-codex-responses` adapter 的 cache 核心机制。它和 direct
OpenAI Responses 不完全相同：

- 必须有非空 `Context.systemPrompt`，作为顶层 `instructions`；
- 必须同时传 `cache_retention != "none"` 和稳定 `session_id`，才会发送
  `prompt_cache_key`；
- `cache_retention="short"` / `"long"` 在 Codex adapter 当前只表示启用 cache key，
  不会发送 `prompt_cache_retention`，因此没有 5 分钟 / 24 小时 TTL 字段；
- adapter 会把同一个 `session_id` 放入 request headers 的 `session_id` 和
  `x-client-request-id`；
- usage 中 `cacheRead > 0` 才证明命中读取；`cacheWrite` 仍应为 0。

#### 5.4.1 Request contract：字段门控（无真实请求）

这个脚本用 fake JWT 和 fake client，只验证 adapter 会不会按条件写 cache 字段，不消耗
真实 API。

```bash
uv run python - <<'PY'
import asyncio
import base64
import json

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage

CLAIM_PATH = "https://api.openai.com/auth"


def jwt_with_account(account_id: str) -> str:
    header = {"alg": "none", "typ": "JWT"}
    payload = {CLAIM_PATH: {"chatgpt_account_id": account_id}}
    return ".".join([
        base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("="),
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("="),
        "signature",
    ])


def context() -> Context:
    return Context(
        systemPrompt="You are testing Codex prompt cache request fields.",
        messages=[UserMessage(content="Say exactly CODEX_CACHE_CONTRACT_OK", timestamp=1)],
    )


async def probe(label: str, options: StreamOptions) -> None:
    captured = {}

    def fake_client(payload, headers):
        captured["payload"] = payload
        captured["headers"] = headers
        return [{"type": "response.completed", "response": {"id": f"resp_{label}"}}]

    options.client = fake_client
    await stream(get_model("openai-codex", "gpt-5.3-codex"), context(), options).result()
    payload = captured["payload"]
    headers = captured["headers"]
    print(label, json.dumps({
        "prompt_cache_key": payload.get("prompt_cache_key"),
        "prompt_cache_retention": payload.get("prompt_cache_retention"),
        "session_id_header": headers.get("session_id"),
        "x_client_request_id": headers.get("x-client-request-id"),
        "has_cache_control": "cache_control" in str(payload),
    }, ensure_ascii=False))


async def main() -> None:
    token = jwt_with_account("acct-cache-contract")
    await probe("none_with_session", StreamOptions(
        api_key=token,
        cache_retention="none",
        session_id="manual-codex-cache-contract",
    ))
    await probe("long_without_session", StreamOptions(
        api_key=token,
        cache_retention="long",
    ))
    await probe("long_with_session", StreamOptions(
        api_key=token,
        cache_retention="long",
        session_id="manual-codex-cache-contract",
    ))


asyncio.run(main())
PY
```

**期望**：

- `none_with_session`：`prompt_cache_key == null`，headers 中无 `session_id`；
- `long_without_session`：`prompt_cache_key == null`，headers 中无 `session_id`；
- `long_with_session`：`prompt_cache_key == "manual-codex-cache-contract"`，
  `session_id_header` 和 `x_client_request_id` 同值；
- 三种情况 `prompt_cache_retention == null`，`has_cache_control == false`。

#### 5.4.2 真实 cache read smoke：payload + response + usage

执行前先完成 5.2，让 `~/.neomagi/auth.json` 或 `NEOMAGI_AUTH_PATH` 中存在
`openai-codex` OAuth credential。这个脚本会发真实 Codex backend 请求。

```bash
unset OPENAI_API_KEY
uv run python - <<'PY'
import asyncio
import json

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage

SESSION_ID = "manual-codex-cache-001"
LONG_PREFIX = "Manual OpenAI Codex prompt cache stable prefix. " * 1200


def assistant_text(result):
    return "".join(block.text for block in result.content if block.type == "text")


def on_payload(payload, model):
    print("PAYLOAD_CACHE_EVIDENCE", json.dumps({
        "model": payload.get("model"),
        "instructions_present": bool(payload.get("instructions")),
        "prompt_cache_key": payload.get("prompt_cache_key"),
        "prompt_cache_retention": payload.get("prompt_cache_retention"),
        "first_input_content": payload["input"][0]["content"][0]["type"],
        "has_cache_control": "cache_control" in str(payload),
    }, ensure_ascii=False))


def on_response(response, model):
    print("REQUEST_HEADER_EVIDENCE", json.dumps({
        "session_id": response.headers.get("session_id"),
        "x_client_request_id": response.headers.get("x-client-request-id"),
        "has_authorization": "Authorization" in response.headers,
    }, ensure_ascii=False))


async def one_call(i: int):
    model = get_model("openai-codex", "gpt-5.3-codex")
    context = Context(
        systemPrompt=f"You are testing Codex prompt cache. Stable prefix:\n{LONG_PREFIX}",
        messages=[UserMessage(content=f"Run {i}: answer with CODEX_CACHE_OK.", timestamp=i)],
    )
    result = await stream(
        model,
        context,
        StreamOptions(
            cache_retention="long",
            session_id=SESSION_ID,
            on_payload=on_payload,
            on_response=on_response,
        ),
    ).result()
    usage = result.usage.model_dump(by_alias=True)
    print("RUN", i, "TEXT", assistant_text(result))
    print("RUN", i, "STOP", result.stop_reason, "USAGE", usage)
    return usage


async def main() -> None:
    usages = []
    for i in range(1, 4):
        usages.append(await one_call(i))
    if all(usage["cacheRead"] == 0 for usage in usages[1:]):
        print("WARN cacheRead stayed zero after warmup; retry later or inspect provider dashboard")
    if any(usage["cacheWrite"] != 0 for usage in usages):
        raise RuntimeError("OpenAI Codex cacheWrite should remain 0")


asyncio.run(main())
PY
```

**期望**：

- 每轮 `PAYLOAD_CACHE_EVIDENCE.prompt_cache_key == "manual-codex-cache-001"`；
- 每轮 `prompt_cache_retention == null`，`has_cache_control == false`；
- `first_input_content == "input_text"`，证明请求使用 Pi/Codex-compatible input shape；
- 每轮 `REQUEST_HEADER_EVIDENCE.session_id` 和 `x_client_request_id` 都等于
  `manual-codex-cache-001`；
- `has_authorization == false`，说明调试回调没有泄漏 OAuth token；
- 每轮 `TEXT` 包含 `CODEX_CACHE_OK` 或含义等价的短答，`STOP stop`，
  `USAGE.totalTokens > 0`；
- 第二轮或第三轮如果出现 `cacheRead > 0`，判定 cache read 成功；
- 如果 payload/header 证据正确但 `cacheRead` 仍为 0，先记录为 provider cache miss /
  延迟命中，不立即判定 adapter 失败。

#### 5.4.3 Codex cache disabled：负向真实 smoke

这个 case 验证即使传了 `session_id`，`cache_retention="none"` 也不会启动 Codex cache。

```bash
unset OPENAI_API_KEY
uv run python - <<'PY'
import asyncio
import json

from ai_provider.api_registry import stream
from ai_provider.model_registry import get_model
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage


def on_payload(payload, model):
    print("PAYLOAD_CACHE_DISABLED", json.dumps({
        "prompt_cache_key": payload.get("prompt_cache_key"),
        "prompt_cache_retention": payload.get("prompt_cache_retention"),
        "has_cache_control": "cache_control" in str(payload),
    }, ensure_ascii=False))


async def main() -> None:
    model = get_model("openai-codex", "gpt-5.3-codex")
    context = Context(
        systemPrompt="You are testing Codex cache disabled behavior.",
        messages=[UserMessage(content="Say exactly CODEX_CACHE_DISABLED_OK", timestamp=1)],
    )
    result = await stream(
        model,
        context,
        StreamOptions(
            cache_retention="none",
            session_id="manual-codex-cache-disabled",
            on_payload=on_payload,
        ),
    ).result()
    print("STOP", result.stop_reason)
    print("USAGE", result.usage.model_dump(by_alias=True))


asyncio.run(main())
PY
```

**期望**：

- `PAYLOAD_CACHE_DISABLED.prompt_cache_key == null`；
- `prompt_cache_retention == null`；
- `has_cache_control == false`；
- `STOP stop`，`USAGE.totalTokens > 0`；
- `cacheRead == 0`，`cacheWrite == 0`。

### 5.5 不在本轮手动测试范围

以下能力等真实 TUI auth / provider runtime 接入后另写验收说明，不作为当前 P1-M2
手动测试 pass/fail 条件：

- TUI `/login` 无环境变量完成 OpenAI OAuth；
- env key 与 login credential 同时存在时的 source 显示和优先级；
- TUI `/logout` 清除 login credential 且不误删 env key；
- TUI 内设置 prompt cache 并展示 `cacheRead/cacheWrite`。

---

## 6. 清理

```bash
unset ANTHROPIC_API_KEY
unset OPENAI_API_KEY
unset OPENAI_CODEX_OAUTH_TOKEN
unset NEOMAGI_MANUAL_OPENAI_KEY
unset NEOMAGI_AUTH_PATH
```

如果 TUI 崩溃导致终端异常：

```bash
reset
```

如果为 5.2/5.4 设置过临时 `NEOMAGI_AUTH_PATH`，测试结束后按需删除该临时 auth 文件。
默认 `~/.neomagi/auth.json` 可能包含真实 OpenAI Codex refresh token，不要提交、复制或贴到日志。

---

## 7. 判定标准

### 当前 M2 可判 pass

- Anthropic env API key 能完成真实 streaming 回复；
- OpenAI env API key 能完成真实 streaming 回复；
- `cacheRetention=none` 不发送任何 cache/session affinity 字段；
- Anthropic long cache payload 有 `cache_control`，真实 usage 能观察到 cache read/write 或有可解释 cache miss；
- OpenAI long cache payload 有 `prompt_cache_key` / `prompt_cache_retention`，真实 usage 能观察到 cache read 或有可解释 cache miss；
- OpenAI OAuth provider 离线单测通过，可选真实登录 smoke 能拿到 account id 且不打印 token；
- OpenAI Codex OAuth credential 能落盘到本地 auth storage，并驱动
  `openai-codex-responses` 真实 streaming；
- OpenAI Codex cache payload/header 有 `prompt_cache_key` / `session_id` /
  `x-client-request-id`，真实 usage 能观察到 `cacheRead > 0` 或有可解释 cache miss；
- Anthropic OAuth 在 P1 core 明确不承诺；
- TUI `/login` / `/logout` 当前只报 stub，不假装成功。

### 不在当前 M2 手动测试范围

- TUI `/login` 真实 OpenAI OAuth；
- TUI credential source 可视化和优先级；
- TUI `/logout` 清除 auth storage credential；
- TUI 内 prompt cache 设置、usage 展示和 provider runtime streaming；
- M1 已覆盖的 TUI lifecycle、fixture playback、editor、hotkeys、`/new`、Ctrl+C、
  终端恢复回归；
  如需复测，执行 `dev_docs/user_tests/p1_m1_manual_test_plan.md`。
