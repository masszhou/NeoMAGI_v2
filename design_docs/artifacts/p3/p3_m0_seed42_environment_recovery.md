---
doc_id: 019e79bb-d194-7313-b340-d6b8c12326b7
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-30T16:33:26+00:00
---
# P3-M0 seed42 环境恢复简短复盘

- Status: draft
- Date: 2026-05-30
- Source logs: `design_docs/artifacts/p3/a6000_naive_seed42*.log`
- Anchor workspace: `/tmp/neomagi_p3_m0/parameter-golf`

## 一句话

seed42 一开始不是训练配置错了，也不是数据错了，而是 Python / PyTorch / Triton 编译环境不稳定。
最终可复现 baseline 环境固定为 **uv standalone CPython 3.12.13 + torch 2.7.1+cu126**，不改
upstream `train_gpt.py`。

## 过程

### 1. 初始环境能启动，但 warmup backward 失败

第一次运行已经读到了正确数据和预算：

```text
train_loader:dataset:fineweb10B_sp1024 train_shards:1
val_loader:... tokens:62021632
max_wallclock_seconds:480.000
seed:42
```

失败点在模型 warmup 的 backward：

```text
RuntimeError: Function CompiledFunctionBackward returned an invalid gradient
got [1, 512] but expected shape compatible with [1, 1, 512]
```

判断：这不是 A6000、FineWeb shard、seed 或 command 参数问题。它发生在
`torch.compile` 生成的 backward graph 里，优先怀疑 PyTorch/Inductor 版本兼容性。

### 2. 切到系统 Python 3.12，先撞到 Triton JIT 头文件问题

下一步尝试用 Python 3.12 环境，减少 Python 3.14 的变量。但系统 Python 3.12 环境缺开发头文件，
Triton 编译 CUDA helper 时失败：

```text
fatal error: Python.h: No such file or directory
torch._inductor.exc.InductorError
```

分析：这是环境构建问题，不是训练脚本问题。Triton 需要编译一个小的 Python C extension，
系统缺 `python3.12-dev` 时会失败。无交互 sudo 不可用，所以不安装系统包。

### 3. 改用 uv standalone Python 3.12.13，排除头文件问题

之后用 uv 下载的 standalone CPython 3.12.13。这个环境自带 `Python.h`，能排除系统头文件缺失：

```text
Python: 3.12.13
Python.h: present
torch: 2.12.0+cu130
cuda_available: True
device: NVIDIA RTX A6000
```

但它仍然复现了第一类错误：

```text
CompiledFunctionBackward returned an invalid gradient
```

结论：根因不只是 Python 3.14，也不是 `Python.h`。关键问题落在 torch 2.12 的 compiled backward
路径和当前 upstream baseline graph 的组合上。

### 4. 固定到 torch 2.7.1 后成功

最后创建新环境：

```text
Python: uv standalone CPython 3.12.13
torch: 2.7.1+cu126
CUDA runtime in torch: 12.6
GPU: NVIDIA RTX A6000
```

这次 seed42 通过了 20 步 warmup，进入主训练，并在 480s wallclock cap 正常停止：

```text
warmup_step:20/20
step:200/20000 val_loss:2.8866 val_bpb:1.7096
step:367/20000 val_loss:2.6526 val_bpb:1.5710 train_time:480405ms
stopping_early: wallclock_cap
final_int8_zlib_roundtrip_exact val_loss:2.69479085 val_bpb:1.59600693
```

结果：seed42 成为有效 baseline sample，后续 seeds 43-46 也在同一环境下完成。

## 最终判断

- 数据路径正确：训练 shard 是 1，validation 是完整 cached FineWeb validation shard。
- 训练命令正确：`MAX_WALLCLOCK_SECONDS=480`、`VOCAB_SIZE=1024`、seed 和 tokenizer 都按 M0 budget。
- 失败原因是环境兼容性：
  - torch 2.12 compiled backward 对这个 graph 失败；
  - 系统 Python 3.12 缺 `Python.h` 会阻断 Triton JIT；
  - uv standalone Python 解决头文件问题，但 torch 2.12 仍失败。
- 成功方案是 pin 环境到 torch 2.7.1，而不是修改 upstream 训练脚本。

## 对后续的影响

P3 baseline 必须引用这个环境版本。以后如果升级 Python、CUDA 或 PyTorch，不能直接沿用这次
baseline 数字；需要重新跑 5-seed baseline replay，并生成新的 baseline ref。
