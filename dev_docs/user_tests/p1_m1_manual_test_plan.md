---
doc_id: 019dc8e5-d120-74c9-a151-ab2770ee4cbb
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-26T10:26:32+02:00
---
# P1-M1 用户手动交互测试说明书

- Status: draft
- Date: 2026-04-26
- Target: P1-M1 TUI Skeleton + Mock Playback Harness（commits `14c2790`、`211b10a`）
- Plan: `dev_docs/plans/p1_m1_tui_skeleton_and_mock_playback.md`
- 适用平台：macOS Terminal / iTerm2 / Ubuntu 常见终端（xterm / gnome-terminal / Alacritty / kitty）
- 不在范围：Windows、真实 provider、真实 agent loop、真实工具执行（M2+）

> M1 是 TUI 壳 + 协议 fixture 回放的最小闭环。手动测试只能验证：CLI 入口、TUI
> 渲染与输入、slash 命令、`--playback` 行为、终端 lifecycle 恢复。任何"和真实
> 模型说话"的预期都不属于 M1。

> **CLI 调用约定**：开发/测试期一律使用 `uv run python -m cli ...`；
> `[project.scripts]` 生成的 `neomagi` shim 留给 M9+ 终端用户安装路径，dev
> 文档不依赖它（仅 §1.3 做一次可选 sanity 检查）。完整规则见 `CLAUDE.md`
> §Commands 与 `AGENTS.md` §实施基线。如果你嫌长，建议自己加一个 shell
> alias，例如 `alias nm='uv run python -m cli'`。

---

## 0. 全新系统准备

### 0.1 Ubuntu（22.04 / 24.04 已验证；其他发行版同理）

```bash
# 0.1.1 系统依赖（Python 3.14 + git + 终端）
sudo apt update
sudo apt install -y git curl build-essential

# 0.1.2 安装 uv（Python 包管理器；项目所有命令都走它）
curl -LsSf https://astral.sh/uv/install.sh | sh
# 让 uv 立刻生效：
source $HOME/.local/bin/env

# 0.1.3 拉仓库（替换为你的实际路径）
git clone <repo-url> NeoMAGI_v2
cd NeoMAGI_v2

# 0.1.4 安装项目依赖（uv 会自动下载 Python 3.14）
uv sync

# 0.1.5 安装 just（项目用它跑标准任务）
sudo snap install just --classic    # snap 路线
# 或：cargo install just              # cargo 路线
# 或：apt install just                # 24.04 之后可用
```

### 0.2 macOS（参考，你这台已就绪）

```bash
brew install uv just git
git clone <repo-url> NeoMAGI_v2
cd NeoMAGI_v2
uv sync
```

### 0.3 安装后烟测（任何平台都跑一遍）

```bash
uv run python -c "import sys; print(sys.version)"   # 3.14.x
uv run pytest tests/                                 # 应 170 passed
just lint                                            # green / regressions=0
uv run python -m cli --help                                # 显示三个 P1 flag
```

如果以上四条任意一条不通过，**先停下来排查环境**，不要继续走交互测试。

### 0.4 测试环境建议

- 用一个**全屏**或至少 100×30 的终端窗口；M1 渲染对宽度有最低要求（substrate
  默认下限 cols≥20、rows≥5，但实际命令需要更宽才看得清）。
- 关闭终端的"自动复制"或"右键菜单"，免得粘贴测试被截断。
- 准备一个**第二个终端**用来：(a) 手动 `kill` 进程；(b) 出问题时跑 `reset`
  恢复终端。

### 0.5 出错应急

如果 TUI 异常崩溃后终端"看起来坏了"（输入不回显、光标消失、粘贴变怪字符），
在该终端里盲打：

```
reset
```

然后回车。`reset` 会重置 termios + 关闭 bracketed paste + 显示 cursor。如果
盲打都不行，开第二个终端跑：

```bash
stty sane < /dev/tty1   # 或对应的终端设备
```

---

## 1. CLI 入口（不进 TUI）

### 1.1 `--help` 输出三个 P1 flag

```bash
uv run python -m cli --help
```

**期望**：`stdout` 包含 `--playback`、`--print`、`--help` 三行；exit code 0。

### 1.2 `--print` stub（M1 无 provider）

```bash
uv run python -m cli --print "hello world"
```

**期望**：`stderr`（不是 stdout）出现：

```
neomagi --print: not implemented in M1 (tracked for M9/M10 once real provider lands).
  echo: hello world
```

文案里的 `neomagi` 是 stub 硬编码的程序名，不是当前调用入口；和你用 `python -m cli` 还是 shim 调用无关。exit code 0。

### 1.3 （可选，M9 前瞻）`neomagi` shim 仍然能调通

仅为确认 `pyproject.toml [project.scripts]` 生成的 console-script 没坏 ——
M1 开发期不依赖它，所以即使 ❌ 也只是低优先记录，不阻塞 acceptance。

```bash
uv run neomagi --help
```

**期望**：和 1.1 完全一致的输出。

---

## 2. TUI 启动 / 渲染 / 退出基础

> 提示：进入 TUI 后看不到 `bash` 提示符是正常的；底部最后一行是 editor，前面
> 是空白消息列。M1 默认**不**进 alt screen，所以退出后你能看到 TUI 渲染过的
> 字符残留 —— 这是设计选择（方便复制日志），不是 bug。

### 2.1 启动 TUI（无 args）

```bash
uv run python -m cli
```

**期望**：
- 终端立刻进入 TUI 模式（光标卡在最后一行的 `> ` 后面）。
- 顶部状态行可能短暂出现"M1 mock — pass --playback or use /play"。
- 底部 footer 行显示 `[idle] M1 mock — pass --playback or use /play`。

### 2.2 退出（`/quit` 路径）

在 TUI 里：

1. 输入 `/quit`，按 `Enter`。
2. 弹出 `Quit NeoMAGI?` 确认框。
3. 按 `Y`（或 Tab 切到 Yes 然后 Enter）。

**期望**：终端立刻回到正常 shell；光标可见；下一次输入回显正常。

### 2.3 退出（`Ctrl+C` idle 路径）

```bash
uv run python -m cli
```

进入 TUI 后**不输入任何字符**，直接按 `Ctrl+C` 一次。

**期望**：立刻退出 TUI，回到 shell；不弹任何对话框。这是 raw mode 下 idle
时 `Ctrl+C` 的"直接退出"约定。

### 2.4 退出（外部 `kill`）

终端 A：

```bash
uv run python -m cli
```

终端 B：

```bash
pgrep -f "cli.__main__" | xargs kill   # 或 `kill -TERM <pid>`
```

**期望**：终端 A 的 TUI 立刻关闭，cursor 可见，cooked mode 恢复。
`kill -9`（SIGKILL）**不**在恢复保证内 —— 内核直接砍进程，`atexit` 跑不到。
不要用 `-9` 测试。

### 2.5 终端恢复验证（关键）

退出 TUI 后，立刻在同一个终端跑：

```bash
stty -a | grep -E "icanon|echo" | head -2
tput cnorm   # cursor visible
echo "✓ terminal looks fine"
printf 'paste test: %s\n' "abc"
```

**期望**：
- `stty -a` 应包含 `icanon` 和 `echo`（即 cooked mode + 回显已恢复）。
- 可以正常输入回显。
- 如果显示 `-icanon` 或 `-echo`，说明 lifecycle 没把 termios 还原 —— 这是
  M1 acceptance #3 的硬要求，必须 fail。

### 2.6 异常退出后的恢复（可选硬核测试）

进入 TUI 后，**不要**通过任何正常路径退出；终端 B 跑：

```bash
pgrep -f "cli.__main__" | xargs kill -TERM
```

`kill -TERM` 会触发 lifecycle 的 SIGTERM handler。终端 A 应当回归 cooked mode。

---

## 3. Editor 输入语义

> 进 TUI（`uv run python -m cli`）后逐项做。每项失败都记下来，**不要**用 `/quit`
> 之外的方式退出（防止终端卡住）。

### 3.1 基础键入

- 输入：`hello world`
- **期望**：`> hello world` 显示在 editor 行。

### 3.2 多行（Shift+Enter）

- 输入：`line one`，按 `Shift+Enter`，输入 `line two`。
- **期望**：editor 显示两行；不会提交。
- **降级备注**：在不支持 xterm `modifyOtherKeys` 的终端，`Shift+Enter` 可能
  退化为普通 `Enter` 直接提交 —— 这是 ADR-0015 §影响段记录的"best-effort"
  行为。如果发生，记下你用的终端型号即可，不阻塞 acceptance。

### 3.3 光标移动

- 输入：`abcdef`
- 按 `←` 三次 → 光标在 `c|def`。
- 按 `Home` → 光标在最前。
- 按 `End` → 光标在最后。
- 按 `Ctrl+A` → 光标在最前；`Ctrl+E` → 光标在最后。

### 3.4 删除

- 输入：`hello`
- `Backspace` 一次 → `hell`。
- 光标移到 `h|ell`，按 `Delete` → `hll`（若你的终端 Delete 键发的是
  `^[[3~`）。

### 3.5 中文 / 宽字符 caret 列号

- 输入：`你好`
- **期望**：caret 落在第 4 列（PROMPT `> ` 占 2 列 + `你好` 占 4 列 → 列 6+1=7
  绝对位置）。视觉上光标紧贴在 `好` 之后。
- 验证：`hello你好` 后再按 `Backspace` 一次应当删除整个 `好`，不是半个。

### 3.6 Bracketed paste（核心）

- 在另一个程序里复制一段**多行**文本（例如 3-4 行 markdown）。
- 在 editor 里 `Cmd+V` / `Ctrl+Shift+V` 粘贴。
- **期望**：整段一次性进入 editor buffer，不会按行触发 `Enter` 提交，不会被
  逐字解释成命令。
- 失败模式：每行末尾自动提交一次（说明 bracketed paste 包络识别坏了）。

### 3.7 历史（M1 仅内存）

- 输入 `hello`，`Enter` 提交（会出 stub 通知，无所谓）。
- 输入 `world`，`Enter` 提交。
- editor 空白时按 `↑` → 应回填 `world`。
- 再按 `↑` → `hello`。`↓` → `world`。再 `↓` → 空。

### 3.8 `Esc` 单按（必须可达）

- 在 editor 里输入 `partial`，**不要按 Enter**，按 `Esc` 一次。
- 等 ~50ms（其实立刻就够，但给自己一个心理缓冲）。
- **期望**：footer 行变为 `[idle] aborted` 或类似；buffer 没有提交。
- 这条是 P1-M1 第一次评审抓到的 bug 修复后的核心回归。如果按一次 Esc 没反应，
  说明 lone-ESC flush 又坏了，记一下。

### 3.9 双 Esc

- 快速按 `Esc Esc`。
- **期望**：顶部 status 出现一条黄色通知：`tree navigation not implemented in M1; tracked in M6`。

---

## 4. Slash 命令（W6 + autocomplete 焦点模型）

> 这是 P1-M1 第二次评审的核心修复区域。重点验证 **autocomplete 不抢焦点**。

### 4.1 输入 `/` 弹出 autocomplete strip

- editor 空白，输入 `/`（一个字符）。
- **期望**：editor buffer 显示 `/`；上方某处出现 selector overlay 列出 22 条
  命令；**光标仍然在 editor**（即下一次按键还是输入到 buffer）。
- 失败模式：光标视觉上跑到 selector 上、或键入 `q` 没反应（说明 selector
  抢了焦点）。

### 4.2 边输入边过滤

- 接 4.1 状态，再输入 `q`。
- **期望**：buffer 变成 `/q`，selector 列表收窄到 `/quit`。

### 4.3 普通输入直接提交（不走 autocomplete picker）

- 接 4.2 状态，依次输入 `u`、`i`、`t`，按 `Enter`。
- **期望**：buffer 在每个字符后变长（`/qu`、`/qui`、`/quit`），selector 自动
  关闭（一旦命令名超出过滤范围）；按 Enter 后弹 `Quit NeoMAGI?` 对话框。
- 按 `N` 取消 → 回到 editor。

### 4.4 Tab 进入 picker → 选 → 回填 editor

- editor 空白，输入 `/`，再输入 `q`。
- 按 `Tab`。
- **期望**：焦点切到 selector（光标视觉切走，再按 `↓` 不动 buffer）。
- 此时按 `Enter`。
- **期望**：editor buffer 变成 `/quit `（带尾随空格），焦点回到 editor，
  selector 关闭。
- 你可以接着按 `Enter` 再走 4.3 的 Confirm 路径，或 `Backspace` 删掉。

### 4.5 Esc 优先关 overlay，不直接 abort

- editor 空白，输入 `/`（overlay 弹出）。
- 按 `Esc` 一次。
- **期望**：overlay 关闭；editor buffer 保留 `/`；footer 不应出现 `aborted`。
- 这条说明 Esc 在 overlay 开启时被 overlay 优先消化，没穿透到 abort 路径。

### 4.6 `/new` —— 实装命令

- 先制造一些消息：`uv run python -m cli --playback tests/fixtures/pi_compat/assistant_text_delta`
  会自动播完退出 —— 不便观察。**改用** TUI 内 `/play` 路径：见 4.9。
- 或直接：在 TUI 里键入 `/new`，按 `Enter`。
- **期望**：消息列被清空；editor footer 提示 "new session (M1 mock — session
  manager arrives in M6)"。

### 4.7 `/hotkeys` —— 实装命令

- 输入 `/hotkeys`，按 `Enter`。
- **期望**：弹出 SettingsList overlay，列出全部默认键位（Enter / Shift+Enter /
  Alt+Enter / Esc / Esc Esc / Tab / Ctrl+C / Ctrl+L / Ctrl+P / Up / Down /
  Left / Right / Home / End / Ctrl+A / Ctrl+E / Backspace / Delete / `/` /
  `@` / `!` / Ctrl+V）。
- 按 `↑`/`↓` 滚动；`Esc` 关闭。

### 4.8 未知命令 → warning 通知

- 输入 `/totally-fake-command`，按 `Enter`。
- **期望**：顶部 status 出现一条黄色通知：`unknown command: /totally-fake-command`。
- editor 回到 idle。

### 4.9 `/play <fixture>` —— 在 TUI 内回放

- 输入 `/play assistant_text_delta`，按 `Enter`。
- **期望**：消息列出现一条 `assistant` 组件，逐字累积出 `Hello, world.`；
  status 出现一条 info 通知：`playback complete: assistant_text_delta`。

也可以试：

| 输入 | 期望 |
| --- | --- |
| `/play assistant_thinking_delta` | 消息列出现 `▸ thinking ...Let me think about this carefully.` 段落 + `Done thinking.` 文本 |
| `/play tool_execution_success` | 出现 `⚙ read({"path":"src/main.py"})` + `result [ok]` 行 |
| `/play parallel_tools` | 出现两条并行 tool 行（`read` + `grep`） |
| `/play compaction` | 出现 `▎ compaction summary  (tokensBefore=80120)` 段 |
| `/play abort_during_stream` | 出现 partial 文本 `This is the first half of the answer.` + 黄色 `[aborted — partial output kept]` |
| `/play abort_during_tool` | tool 行加 `[aborted]` 标记 |
| `/play nonexistent` | 红色错误通知 `fixture 'nonexistent' not found` |

### 4.10 stub 命令（18 条）

- 输入任何 stub 命令，例如 `/compact` `/login` `/resume` `/export` 等。
- **期望**：status 显示一条 info 通知，形如：
  `/compact not implemented in M1; tracked in M7 (Manual compaction)`。

### 4.11 `@` / `!` 触发器

- 输入单个 `@`：buffer 显示 `@`，status 出现：`@-mention autocomplete not implemented in M1; tracked in M5`。
- 输入单个 `!`：buffer 显示 `!`，status 出现：`!shell mode not implemented in M1; tracked in M5`。
- 这条只是验证 trigger 不会再吞字符。

### 4.12 `Ctrl+V` 图片粘贴占位

- 按 `Ctrl+V`（注意是 `Ctrl`，不是 macOS 的 `Cmd`）。
- **期望**：status 出现：`image paste deferred to M2/M5; placeholder only`。
- buffer 不变（不会插入字符，因为 `Ctrl+V` 不是可打印字符）。

---

## 5. `--playback` 端到端

### 5.1 短 fixture 自动播放并退出

```bash
uv run python -m cli --playback tests/fixtures/pi_compat/assistant_text_delta
```

**期望**：
- 进入 TUI；editor footer 显示 `playback: assistant_text_delta`。
- 看到 `Hello, world.` 渐进出现。
- 整体在 ~1 秒内结束并自然退出回 shell。
- **退出后 `stty -a` 应仍然 cooked mode**。

### 5.2 abort_during_stream 看 partial 保留

```bash
uv run python -m cli --playback tests/fixtures/pi_compat/abort_during_stream
```

**期望**：
- 看到 `This is the first half of the answer.` 出现。
- 紧接着出现黄色 `[aborted — partial output kept]`。
- 自动退出。

### 5.3 tool 中途 abort

```bash
uv run python -m cli --playback tests/fixtures/pi_compat/abort_during_tool
```

**期望**：
- tool 行渲染 `read({"path":"src/very_large.py"})` + `partial: ...`。
- 紧接着出现 `[aborted]` 标记。
- 自动退出。

### 5.4 不存在的 fixture 也不挂

```bash
uv run python -m cli --playback /tmp/no-such-fixture
```

**期望**：立刻报错并退出（exit 0）；stderr 出现：
`neomagi --playback: failed to load fixture /tmp/no-such-fixture`。

如果挂超过 10 秒，按 `Ctrl+C` 抢救，并标记此项失败。

### 5.5 全部 6 条 M1 fixture 的 smoke

按顺序跑：

```bash
for f in assistant_text_delta assistant_thinking_delta parallel_tools \
         compaction abort_during_stream abort_during_tool; do
  echo "=== $f ==="
  uv run python -m cli --playback "tests/fixtures/pi_compat/$f"
  echo "exit=$?"
  sleep 0.5
done
```

**期望**：每条都自然退出，`exit=0`。

---

## 6. 终端 resize

### 6.1 拉宽

- 启动 `uv run python -m cli`。
- 用鼠标把终端窗口横向拉宽 30 列（或 `printf '\e[8;30;200t'`，不是所有终端
  都支持）。
- **期望**：界面立刻 full redraw，没有残影。

### 6.2 缩窄

- 反向缩窄 20 列。
- **期望**：edit 行能正确按新宽度 wrap；footer 截断到新宽度；不出现"上一帧
  的尾巴"挂在右边。
- 如果有显著残影或闪烁，记下当前终端类型 + 窗口尺寸。

### 6.3 极小窗口

- 缩到 ~30 列宽。
- **期望**：所有渲染仍按列宽截断，不会爆出宽度。

---

## 7. lifecycle 回归（终端恢复硬要求）

把 2.5 的 termios 检查重复 5 次，分别覆盖：

| 退出路径 | 步骤 | 验证 |
| --- | --- | --- |
| `/quit` 确认 | TUI 内 `/quit Enter Y` | `stty -a \| grep icanon` 含 `icanon` |
| `Ctrl+C` idle | TUI idle 时按 `Ctrl+C` | 同上 |
| `kill -TERM` | 终端 B 跑 `pgrep -f cli.__main__ \| xargs kill -TERM` | 同上 |
| 异常崩溃 | 暂时不易手动触发 — 依赖 `tests/tui/test_lifecycle.py::test_lifecycle_runs_exit_on_exception` | 自动测试已覆盖 |
| `--playback` 自然结束 | `uv run python -m cli --playback ...assistant_text_delta` 跑完 | 同上 |

任何一条 `stty -a` 显示 `-icanon` 或 cursor 仍隐藏 → **acceptance fail**，
立即跑 `reset` 救场并标记。

---

## 8. 已知不支持 / 不在 M1 范围

下列行为**不要**当 bug 报：

- 真实 LLM 回复：M2 才接 provider。
- `--print` 真的执行：M9/M10。
- `/login` `/logout` `/model` `/settings` 等 18 条 stub：按设计返回 "tracked in
  M{X}" 通知。
- `/resume` `/fork` `/clone` `/tree`：M6 session manager 才接。
- `/compact`：M7 才有真实 compaction。
- `/share` `/export`：M10 才有 JSONL/HTML 导出。
- 真正的 inline image 显示（Kitty / iTerm 协议）：M2/M5 才接，目前一律
  `[image: ... (terminal preview unavailable)]` 占位。
- Windows 终端：ADR-0015 明确不在 M1 支持范围。
- 真实 `@`-mention 文件 fuzzy 自动补全：substrate 已就位（`tui.autocomplete.
  file_completions`），但 controller 没接 overlay；M5 把 file picker 接上。
- 真实 `!shell` 模式：M5。

---

## 9. 报告模板

把你跑完的结果以下列格式贴给我（或自己开 issue）。每条只填 ✅/❌ + 一行备注：

```
## P1-M1 manual test report — <date>, <terminal-name>

环境
- OS: <Ubuntu 24.04 / macOS 15.x / ...>
- Terminal: <gnome-terminal 3.50 / iTerm2 3.5 / ...>
- 终端尺寸: <120x40>

§0 安装
- 0.3 烟测: ✅/❌  备注: ...

§1 CLI
- 1.1 --help: ✅/❌
- 1.2 -m cli: ✅/❌
- 1.3 --print: ✅/❌

§2 启动 / 退出
- 2.1: ✅/❌
- 2.2 /quit: ✅/❌
- 2.3 Ctrl+C idle: ✅/❌
- 2.4 kill -TERM: ✅/❌
- 2.5 终端恢复: ✅/❌
- 2.6 异常退出恢复: ✅/❌

§3 Editor
- 3.1 - 3.9: 列每条结果

§4 Slash
- 4.1 - 4.12: 列每条结果

§5 --playback
- 5.1 - 5.5: 列每条结果

§6 Resize
- 6.1 - 6.3: 列每条结果

§7 Lifecycle 回归
- 五条退出路径 stty 验证: 列结果

发现的问题
- <一行说明 + 复现步骤>
- ...
```

任何 ❌ 都希望看到：复现步骤 + 当时 terminal type + 是否 `reset` 能恢复。
