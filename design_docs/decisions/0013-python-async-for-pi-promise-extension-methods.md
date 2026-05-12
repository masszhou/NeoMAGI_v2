---
doc_id: 019dc656-609a-774d-bbda-8afd77ec1004
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-25T22:30:35+02:00
---
# 0013-python-async-for-pi-promise-extension-methods

- Status: accepted
- Date: 2026-04-25
- Related: `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md`
- Related: `design_docs/decisions/0010-use-pydantic-v2-for-protocol-types.md`
- Related: `design_docs/decisions/0012-python-native-extension-mvp-boundary.md`
- Architecture: `design_docs/architecture/pi_behavior_matrix.md`

## 选了什么

- Python extension Protocol 复刻 Pi runtime-control API 时，凡本 ADR 覆盖的方法在 pi-mono TypeScript baseline 中标为 `Promise<X>`，Python mirror 一律声明为 `async def ... -> X`；实现层也必须是 `async def`，调用方必须 `await`。
- P1-M0 明确锁定以下异步槽位（本 ADR 的完整范围）：
  - `ExtensionCommandContext`: `wait_for_idle` / `new_session` / `fork` / `navigate_tree` / `switch_session` / `reload`
  - `ExtensionAPI`: `exec` / `set_model`
- Pi baseline 中返回同步值的 API 继续保持同步 `def`，例如 `register_*`、`get_*`、`set_active_tools`、`set_thinking_level`、`send_message`、`send_user_message`、`append_entry`、`set_label`、`compact`、`is_idle`、`abort`、`shutdown`。
- `async def -> X` 的返回类型表示 await 后的 resolved value；不要把签名写成同步 `def -> Awaitable[X]`，也不要用同步 `def -> X` 冒充异步槽位。
- Extension handler / factory 是否允许同步或异步仍按 ADR-0012；本 ADR 只约束 NeoMAGI 提供给 extension 的 API method surface。
- `ExtensionUIContext` 中同样返回 `Promise<X>` 的 dialog / overlay 方法不在本 ADR 范围内；M1/M3 若将 UI context 纳入 Pi-compatible Protocol，必须显式同步类型和 behavior matrix。若没有新的取舍，可沿用本 ADR 的 async 原则并在实现 PR 中引用本 ADR。

## 为什么

- ADR-0009 要求 Pi-compatible contract。Pi extension 作者看到 `Promise<X>` 就会按异步调用和取消边界理解；Python mirror 应保留同等语义。
- Python 结构化类型中，同步 `def -> X` 与 `async def -> X` 不等价。前者运行时返回 `X`，后者返回 coroutine，必须 `await` 后才得到 `X`。先写同步再在 M3 改异步会破坏 Protocol contract。
- Command/session mutation、shell exec、model switching 都可能等待 extension cancellation、policy/audit、IO 或 runtime 状态转换；异步边界符合真实执行模型。
- 保持同步 API 仍同步，可以避免把 `register_*`、简单 getter/setter、editor text mutation 等无等待语义的方法过度异步化。

## 放弃了什么

- 方案 A：M0 先用同步 Protocol，M3 实现时再改成 `async def`。
  - 放弃原因：会让 M0 类型契约与未来 runtime 不一致，并且在 Python 类型系统中不是兼容替换。
- 方案 B：同步 `def` 返回 `Awaitable[X]`。
  - 放弃原因：调用体验和 Pi 的 `await api.method()` 类似，但实现仍容易被写成普通函数返回裸值；`inspect.iscoroutinefunction` 也无法作为验收门槛。
- 方案 C：把整个 ExtensionAPI / ExtensionContext 都改成 async。
  - 放弃原因：Pi 本身不是全异步 API；无等待语义的注册、getter、setter 保持同步更简单，也减少 runtime 调度复杂度。
- 方案 D：Python 自行发明同步 convenience API，并让 extension 作者混用。
  - 放弃原因：会分裂兼容核心与 NeoMAGI-only surface。若未来需要 convenience wrapper，必须作为显式辅助层，不进入 Pi-compatible Protocol。
- 方案 E：在本 ADR 内顺手决定 `ExtensionUIContext` 的 dialog / overlay async 形态。
  - 放弃原因：这次取舍来自 command/session mutation、`exec`、`setModel` 的 runtime-control 契约；UI context 的阻塞/异步体验应随 M1/M3 UI harness 设计同步落地，避免本 ADR 承诺未实现的 UI surface。

## 影响

- `packages/magipi/src/cli/extensions/types.py` 中本 ADR 覆盖的 Pi `Promise<X>` mirror 必须通过 `inspect.iscoroutinefunction(...)` 验收；同步槽位必须保持非 coroutine function。
- `pi_behavior_matrix.md` § D 必须列出 async / sync 分组，作为 M3 runtime 和后续 review 的对照表。
- M3 实现 `ExtensionAPI` / `ExtensionCommandContext` 时，异步方法必须在内部串接 cancellation、policy/audit、session mutation 和 runtime lifecycle，并返回 await 后的 Pi-compatible result shape。
- 测试应至少覆盖：
  - command/session mutation methods 是 coroutine function；
  - `exec` / `set_model` 是 coroutine function；
  - `register_*`、getter、普通 setter、`send_message` 等同步槽位不是 coroutine function。
- 若未来 pi-mono baseline 升级改变某个方法的 `Promise<X>` 状态，必须按 ADR-0011 走 baseline diff review，并同步更新本 ADR 的影响清单或追加新 ADR。
