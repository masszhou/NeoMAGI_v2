---
doc_id: 019dc65c-ed21-7326-89df-f58d31be1a04
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T22:37:48+02:00
---
# 0014-extend-async-protocol-rule-to-extension-ui-context

- Status: accepted
- Date: 2026-04-25
- Amends: `design_docs/decisions/0013-python-async-for-pi-promise-extension-methods.md` § 放弃了什么 方案 E
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Related: `design_docs/decisions/0012-python-native-extension-mvp-boundary.md`
- Architecture: `design_docs/architecture/pi_behavior_matrix.md`

## 选了什么

- 把 ADR-0013 锁定的 "Pi `Promise<X>` → Python `async def -> X`" 规则正式扩展到 `ExtensionUIContext`。
- 异步槽位（5 个 dialog / overlay）：
  - `select(title, options, opts?): Promise<string | undefined>`
  - `confirm(title, message, opts?): Promise<boolean>`
  - `input(title, placeholder?, opts?): Promise<string | undefined>`
  - `custom<T>(factory, options?): Promise<T>`
  - `editor(title, prefill?): Promise<string | undefined>`
- 其余 19 个 `ExtensionUIContext` 方法（`notify` / `on_terminal_input` / 各类 `set_*` / `paste_to_editor` / `get_editor_text` / `set_editor_component` / `theme` 系列 / `get_tools_expanded` / `set_tools_expanded` 等）继续保持同步 `def`，与 Pi 上游 `void` / 普通返回值一致。
- ADR-0013 § 影响中"`inspect.iscoroutinefunction(...)` 必须通过"的验收门槛同样适用于本 ADR 覆盖的 5 个 UI 异步槽位。

## 为什么

- ADR-0013 把 UI dialog / overlay 的 async 形态显式列为"放弃方案 E"，留给 M1/M3 决定。M0 review 期间为了让协议层契约一致，已经把这 5 个方法落实为 `async def`，并把"所有 Pi `Promise<X>` 都用 `async def`"写成 `pi_behavior_matrix.md` § D 的项目级规则。
- 实现已经做、文档已声明项目级规则，但决策日志仍保留方案 E 的 defer 状态，会让 contract 与 ADR 不一致。补本 ADR 把决策落到日志里，避免后续 reviewer 把 ADR-0013 的 defer 当成现行约束。
- ADR-0009 product equivalence contract 要求 Pi 心智模型可迁移：Pi 作者看 dialog Promise 期待 await + cancellation；同步 Python `def -> str | None` 没有这层语义。
- 所有 5 个 dialog 方法都阻塞等待用户响应（`select` / `confirm` / `input` / `editor`）或一段持续时间内的自定义组件交互（`custom`），异步是真实执行模型，不是为对齐而对齐。

## 放弃了什么

- 方案 A：维持 ADR-0013 的 defer，直到 M3 再一起决定。
  - 放弃原因：M0 已落代码与项目级规则；继续 defer 会让 ADR / 代码 / matrix 三者长期错位，新进 reviewer 容易再次提出本问题。
- 方案 B：把整个 `ExtensionUIContext` 都改成 async（含 `notify` / `set_*` 等）。
  - 放弃原因：Pi 上游这些方法返回 `void` / 普通值，没有 await 语义；过度异步化会增加 extension 调用噪音和 runtime 调度复杂度，与 ADR-0013 的同步原则冲突。
- 方案 C：直接修订 ADR-0013，把方案 E 改成"接受"。
  - 放弃原因：ADR-0001 的轻量约定是"决策不回写"；保持 0013 历史快照、用 0014 显式 amend 更可追溯。
- 方案 D：把 `set_theme` 也纳入本 ADR。
  - 放弃原因：`setTheme` Pi 是同步返回 `{success, error?}`，不是 `Promise`，不属于本规则覆盖范围。`set_theme` 返回类型修正（`-> dict[str, Any]`）作为同期 contract 校正在 matrix § D.3 直接落地，不需要 ADR 决策。

## 影响

- `packages/neomagi_pi/src/cli/extensions/types.py` 中 `ExtensionUIContext` 的 5 个异步槽位必须通过 `inspect.iscoroutinefunction(...)`；其余 UI 方法必须保持非 coroutine function。
- `pi_behavior_matrix.md` § D"Async 约定"段必须同时引用 ADR-0013 与本 ADR 作为权威来源；§ D.3 表格必须显式标注 5 个 UI 异步行。
- M3 实现 UI runtime 时，dialog 方法必须支持 await + cancellation（与 `signal: AbortSignal` 协作），不可在内部用 `asyncio.run` 阻塞主循环。
- 若未来 pi-mono baseline 升级改变 UI 方法的 `Promise<X>` 状态，按 ADR-0011 走 baseline diff review，并同步更新本 ADR 的影响清单或追加新 ADR。
- 本 ADR 不改变 ADR-0013 已锁定的 8 个异步槽位（`ExtensionAPI.exec` / `set_model` + `ExtensionCommandContext` 6 个），只把 UI 5 个补上，最终项目级 async 槽位共 13 个。
