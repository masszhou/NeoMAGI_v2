---
doc_id: 019dc61c-fd9e-77a5-aeb9-add2c65a22fd
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T21:28:01+02:00
---
# 0012-python-native-extension-mvp-boundary

- Status: accepted
- Date: 2026-04-25
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Related: `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md`
- Architecture: `design_docs/architecture/pi_behavior_matrix.md`

## 选了什么

- Extension runtime 第一版采用 Python-native `.py` 文件，入口为 `setup(api)`；`setup` 和 handler 均允许同步或异步。
- 第一版只实现 Pi extension 的核心事件中间件语义：`api.on(event, handler)`、`tool_call` 可原地修改或 block、`tool_result` 可 patch、extension 可注册 tool / slash command / flag，并可追加少量 session custom entry。
- Extension 加载来源先限制为全局目录、项目目录和显式 `--extension` 文件；加载顺序同时决定 handler 执行顺序。
- Python 实现必须沿用已声明的 Pi-compatible Protocol、事件名、字段名和 result shape。MVP 可以少实现能力，但不能发明不兼容命名或 wire shape。
- Extension Python 文件本身按 trusted local code 处理；但 extension 注册的 tool / command / event handler 不能绕过 NeoMAGI 的 policy、audit 和 session 持久化边界。

## 为什么

- NeoMAGI 需要先验证 extension 的最小闭环，而不是复刻 Pi 的 TypeScript / Node 加载机制。
- `.py setup(api)` 能直接服务 Python agent runtime，减少跨语言加载、依赖隔离、调试和发布复杂度。
- Extension 的核心价值在生命周期插入、tool call 拦截、tool/command 扩展和 session 状态追加；这些能力足以支撑 M3 的主要验收夹具。
- 保持 Pi-compatible 命名和 wire shape，可以继续复用已冻结的 behavior matrix、fixture 和后续迁移判断。

## 放弃了什么

- 方案 A：兼容 Pi 的 TypeScript / JavaScript extension 加载。
  - 放弃原因：需要嵌入 Node / jiti / npm 包解析，第一版复杂度高，且不提升 Python runtime 的最小闭环验证。
- 方案 B：第一版实现完整 Pi ExtensionAPI。
  - 放弃原因：API surface 大，会推迟 `tool_call`、`tool_result`、command 和 session entry 等核心语义落地；未实现能力应显式标注或 stub，而不是阻塞最小 runtime。
- 方案 C：重新设计 NeoMAGI-only extension API。
  - 放弃原因：会破坏 ADR-0009 的 product-equivalent + contract-stable 方向，让 Pi-compatible fixture 和 behavior matrix 失去边界价值。
- 方案 D：为 extension 自身实现权限沙箱或子进程协议。
  - 放弃原因：这是后续安全增强，不是 MVP 必需；第一版先把 extension tool/command 纳入既有 policy/audit。

## 影响

- M3 extension 开发计划应先实现 `ExtensionManager`、`ExtensionAPI` binding、`.py` loader、handler ordering 和事件 result 应用规则。
- 第一批事件优先覆盖 `session_start`、`session_shutdown`、`input`、`before_agent_start`、`context`、`tool_call`、`tool_result`、`agent_start`、`agent_end`；流式 UI 事件可随 agent loop 能力补齐。
- `input`、`tool_call`、`tool_result`、`before_agent_start` 等事件结果必须以 `src/cli/extensions/types.py` 和 behavior matrix 为准；例如 `input` 使用 `type` 字段，不引入 `action` 别名作为 contract。
- `before_provider_request` 第一版可以允许原地修改 payload；若要支持 handler 返回新 payload dict，必须同步更新 Protocol、behavior matrix 和 fixture。
- Flag 类型第一版保持 Pi-compatible 的 `boolean | string`；`number` 属于后续 NeoMAGI 扩展，不能混入兼容核心。
- Path guard 等安全 extension 示例必须使用可靠路径归属判断，例如 `Path.relative_to()`，不能使用字符串 `startswith()` 判断 workspace 边界。
