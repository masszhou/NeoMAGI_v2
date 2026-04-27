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
> OAuth provider core、prompt cache payload/usage normalization；但 TUI 仍是 M1 mock
> shell，尚未接真实 agent runtime。`/login`、`/logout` 目前只是 slash command
> stub，尚未接 auth storage / OAuth provider。
>
> 因此本文分两类：
> - **当前可执行**：用 Python provider runtime 做真实 API smoke、prompt cache 验证。
> - **OpenAI OAuth core**：当前可用离线单测和可选真实登录 smoke 验证；尚不落盘。
> - **TUI 登录验收目标**：等 `/login` 接真实 auth storage / OpenAI OAuth 后执行；
>   当前只验证 stub 行为不误导用户。

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
| A1 | anthropic | env `ANTHROPIC_API_KEY` | `claude-3-5-haiku-20241022` | none | n/a | pass/fail | `cacheRead/cacheWrite` |
| O1 | openai | env `OPENAI_API_KEY` | `gpt-4o-mini` | none | n/a | pass/fail | `cacheRead/cacheWrite` |

---

## 1. 当前可执行：环境变量 API Key 登录路径

M2 的 credential 顺序是：

1. `StreamOptions.api_key` 显式传入；
2. provider 环境变量；
3. 否则报错。

当前 provider 环境变量名：

- Anthropic: `ANTHROPIC_API_KEY`
- OpenAI: `OPENAI_API_KEY`

### 1.1 缺失 key 的负向检查

```bash
unset ANTHROPIC_API_KEY
unset OPENAI_API_KEY

uv run python - <<'PY'
from ai_provider.credentials import resolve_api_key
from ai_provider.model_registry import get_model

for provider, model_id in [
    ("anthropic", "claude-3-5-haiku-20241022"),
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
    model = get_model("anthropic", "claude-3-5-haiku-20241022")
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
    result = await result_stream.result()
    print("\nEVENTS", seen)
    print("STOP", result.stop_reason)
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
    result = await result_stream.result()
    print("\nEVENTS", seen)
    print("STOP", result.stop_reason)
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
2. **usage 证据**：provider 返回的 usage 中 `cacheRead` 或 `cacheWrite` 非零。

如果 usage 仍为 0，但 payload 正确，先把 prefix 加长、重复第二次请求、检查 provider
dashboard。cache miss 是正常 provider 行为，不等价于 NeoMAGI bug。

### 2.1 Anthropic prompt cache：payload + usage

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


async def one_call(i: int):
    model = get_model("anthropic", "claude-3-5-haiku-20241022")
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
- 第一轮常见 `cacheWrite > 0`，第二轮常见 `cacheRead > 0`；
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
    model = get_model("anthropic", "claude-3-5-haiku-20241022")
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

### 3.2 Abort / Ctrl+C（当前 TUI mock）

真实 provider 尚未接 TUI，所以当前只能验证 M1 abort/exit 行为：

```bash
uv run python -m cli
```

在 idle 状态按 `Ctrl+C`。

**期望**：进程退出；shell 接管；`stty -a` 中 `icanon`、`echo`、`isig` 都恢复。

等 M4 TUI 接真实 runtime 后，补测：

1. 用真实 provider 发一个长输出 prompt；
2. 输出中途按 `Ctrl+C`；
3. 第一次 `Ctrl+C` 应 abort 当前请求并保留 partial text；
4. idle 后再次 `Ctrl+C` 才退出 TUI。

---

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

## 5. OpenAI OAuth / TUI 登录验收目标

P1 core OAuth scope：

- **OpenAI**：实现并测试 OpenAI OAuth provider core；TUI `/login` 之后接入。
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

### 5.2 当前可执行：OpenAI OAuth provider 真实登录 smoke（可选）

本 smoke 只验证 OAuth flow 能拿到 OpenAI/Codex credential，不写入 auth storage，也不把 token
打印出来。执行前确保没有其他进程占用 `127.0.0.1:1455`。

```bash
unset OPENAI_API_KEY
uv run python - <<'PY'
import asyncio
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
    print(
        {
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
- 退出后 repo 内没有新增 secret 文件。

### 5.3 后续目标：OpenAI `/login` 无环境变量

准备：

```bash
unset ANTHROPIC_API_KEY
unset OPENAI_API_KEY
uv run python -m cli
```

步骤：

1. 输入 `/login`。
2. 如果有 provider picker，选择 `OpenAI`；如果实现为带参数命令，输入
   `/login openai`。
3. 完成浏览器 / device code / subscription flow。
4. 回到 TUI，发送：`Say exactly OPENAI_LOGIN_OK`。

**期望**：
- TUI 明确显示 active credential source 为 OpenAI login / subscription；
- 不要求 `OPENAI_API_KEY` 存在；
- assistant 正常 streaming；
- final message 记录 provider/model/usage；
- `/logout` 后旧 credential 不再可用。

### 5.4 后续目标：Env key 与 `/login` 同时存在时的可见性

准备：

```bash
export OPENAI_API_KEY='sk-test-env-key'
uv run python -m cli
```

步骤：

1. `/login openai` 或在 picker 中选择 OpenAI 登录；
2. 打开 `/model` 或 `/settings` 中的 credential/status 区；
3. 发送一次短 prompt。

**期望**：
- UI 必须明确显示本次请求使用 env key 还是 login credential；
- 如果实现有优先级，优先级必须稳定且可解释；
- 不允许“env 与 subscription 混用但 UI 不显示”。

### 5.5 后续目标：`/logout` 不应清除 env key

准备：

```bash
export OPENAI_API_KEY='sk-valid-env-key'
uv run python -m cli
```

步骤：

1. `/login openai`；
2. `/logout openai` 或 `/logout` 后选择 OpenAI；
3. 发送短 prompt。

**期望**：
- `/logout` 只清除 TUI/auth-storage credential；
- 如果 env key 仍存在，UI 可以回退到 env key，但必须显示 source changed；
- 如果产品决定 logout 后禁用该 provider，则也必须显式说明，不得静默失败。

### 5.6 后续目标：TUI prompt cache 的 OpenAI login credential 路径

对 OpenAI login 执行：

1. 确认无环境变量 key。
2. 通过 `/login` 完成订阅登录。
3. 在 `/settings` 或等价界面设 `cacheRetention=long`。
4. 连续发送两轮带相同长 prefix 的 prompt。
5. 查看 UI usage 面板、debug log 或 session detail。

**期望**：
- OpenAI：请求使用 stable `sessionId` / `prompt_cache_key`；第二轮常见
  `cacheRead > 0`；`cacheWrite == 0`（Responses path）；
- UI 必须能展示或导出 usage 的 `cacheRead/cacheWrite`，否则用户无法判断 prompt
  cache 是否成功。

---

## 6. 其他 TUI 手动交互覆盖

这些 case 继承 M1 手测，但在 M2/M4 之后仍要保留，防止真实 provider 接入破坏 TUI
lifecycle。

### 6.1 启动 / 退出 / 终端恢复

```bash
uv run python -m cli
```

覆盖：

- `/quit` → Confirm overlay → `Y` + Enter；
- idle 下 `Ctrl+C` 退出；
- 外部 `kill` 默认 SIGTERM 后终端恢复；
- 退出后执行：

```bash
stty -a | grep -E "icanon|echo|isig" | head -2
echo TTY_OK
```

**期望**：`icanon`、`echo`、`isig` 都不带 `-`；`echo TTY_OK` 正常回显。

### 6.2 Slash command 表与 stub 诚实性

在 TUI 中输入 `/`，观察 autocomplete。

**期望**：
- `/new`、`/hotkeys`、`/quit` 可用；
- `/login`、`/logout` 当前为 M9 stub；
- 未实现命令必须说明 tracked milestone，不允许假成功。

### 6.3 `/hotkeys`

输入：

```text
/hotkeys
```

**期望**：打开热键 overlay；Esc 可关闭；关闭后 editor focus 恢复。

### 6.4 `/new`

先用 `/play assistant_text_delta` 或普通输入制造可见消息，再输入：

```text
/new
```

**期望**：消息列清空；footer 显示 new session 说明；终端不闪屏、不破坏输入焦点。

### 6.5 `/play` fixture 回放

```bash
uv run python -m cli
```

在 TUI 内执行：

```text
/play assistant_text_delta
/play assistant_thinking_delta
/play assistant_tool_call
/play parallel_tools
/play abort_during_stream
```

**期望**：
- text / thinking / tool call / parallel tool / abort 都走同一渲染路径；
- 回放结束后回到 idle；
- 中途 `Ctrl+C` 能 abort 当前播放或退出，不破坏终端。

### 6.6 输入编辑与长文本

在 TUI editor 中手动测试：

- 普通英文输入、Backspace、左右移动；
- 粘贴 10 行文本；
- 粘贴含中文 / emoji 的文本；
- 窄窗口和宽窗口下 resize；
- 输入 `@`、`!`、`/` 触发各自提示。

**期望**：
- 不出现字符错位、残留、光标跳错行；
- 未实现功能只显示清晰 stub；
- resize 后 footer/editor/message list 不互相覆盖。

### 6.7 真实 runtime 接入后的交互回归

等 M4 接入真实 runtime 后补测：

- 在 TUI 内选择 Anthropic env key，连续两轮对话；
- 在 TUI 内选择 OpenAI env key，连续两轮对话；
- 用 `/model` 切换 provider/model；
- 同一 session 第二轮保持稳定 provider cache affinity；
- `/new` 后新 session 不复用旧 session affinity，除非产品明确设计为复用；
- streaming 过程中 Ctrl+C abort，不退出；
- provider error 显示为可读错误，终端仍恢复。

---

## 7. 清理

```bash
unset ANTHROPIC_API_KEY
unset OPENAI_API_KEY
unset NEOMAGI_MANUAL_OPENAI_KEY
```

如果 TUI 崩溃导致终端异常：

```bash
reset
```

如果执行过 `/login`，测试结束时必须 `/logout`，并确认后续无环境变量时无法继续静默调用
provider。

---

## 8. 判定标准

### 当前 M2 可判 pass

- Anthropic env API key 能完成真实 streaming 回复；
- OpenAI env API key 能完成真实 streaming 回复；
- `cacheRetention=none` 不发送任何 cache/session affinity 字段；
- Anthropic long cache payload 有 `cache_control`，真实 usage 能观察到 cache read/write 或有可解释 cache miss；
- OpenAI long cache payload 有 `prompt_cache_key` / `prompt_cache_retention`，真实 usage 能观察到 cache read 或有可解释 cache miss；
- OpenAI OAuth provider 离线单测通过，可选真实登录 smoke 能拿到 account id 且不打印 token；
- Anthropic OAuth 在 P1 core 明确不承诺；
- TUI `/login` / `/logout` 当前只报 stub，不假装成功；
- TUI lifecycle 仍满足 M1 终端恢复要求。

### 未来 TUI auth 可判 pass

- Anthropic env key 路径能独立跑通；Anthropic `/login` 不属于 P1 core；
- OpenAI env key 与 OpenAI `/login` 两条路径都能独立跑通；
- UI 明确显示 active credential source；
- `/logout` 清除 login credential 且不误删 env key；
- TUI 中 prompt cache 成功可通过 usage 或导出日志验证；
- 真实 provider streaming、abort、model switch、session/new-session cache affinity 都不破坏 TUI。
