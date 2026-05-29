---
doc_id: 019e743a-1f4b-7698-9cb6-cda87e54d9e2
doc_id_format: uuidv7
doc_id_assigned_at: 2026-05-29T09:58:53+02:00
---
# Mini Parameter Golf: 算力换算与执行预算配置

## 状态

- Status: draft reference（第二层 architecture 之前的配置参考，第一层 roadmap 的补充）
- Date: 2026-05-28
- Document: `design_docs/references/reference_mini_parameter_golf_budget.md`
- Related roadmap: `design_docs/roadmap/p3_experiment_loop_mvp.md`
- Upstream: [openai/parameter-golf](https://github.com/openai/parameter-golf)
- 范围：本文档只回答 "P3 anchor 沙盒怎么在低算力下跑通"。
       不重建框架、不改 train_gpt.py、不自建 dataset/eval。
       直接用 parameter-golf repo 现成的 knob。

---

## 0. 文档定位

P3 roadmap 把 anchor 定为 Mini Parameter Golf，但留了一个未解的问题（roadmap §7-Q1）：
mini budget 到底多少？跑在哪台机器？naive baseline 实测 val_bpb 是多少？

本文档把这个空缺填上，给出可直接执行的配置。它**不是**独立项目设计——
那种"自建 dataset、自定 artifact cap、自建 leaderboard"的做法在上一轮讨论中明确推到 P3 之后。
本文档的全部内容就是：**怎么在小算力下用现成的 parameter-golf repo 跑通 agent 实验闭环。**

---

## 1. 硬件现实

### 1.1 原始挑战的算力规模

```text
官方 leaderboard 提交规则：
  硬件：8xH100 SXM（NVLink）
  训练时间上限：10 minutes wall-clock
  评估时间上限：另外 10 minutes
  artifact 上限：16,000,000 bytes（code + 压缩模型）
  validation：FineWeb 验证集前 50k docs，bits-per-byte，tokenizer-agnostic

naive baseline 在此规模下：val_bpb = 1.2244（9L 512d sp1024 TiedEmb 4KV）
当前 SOTA：val_bpb ≈ 1.08（多层叠加 GPTQ + TTT + parallel residuals 等）
```

### 1.2 单卡 A6000 vs 8xH100 算力换算

```text
                    BF16 tensor TFLOPS    HBM 带宽          VRAM
H100 SXM 单卡        ~989                 3,350 GB/s        80GB HBM3
8xH100 SXM 总和      ~7,912               跨卡受 NVLink     640GB
A6000 单卡           ~155                 768 GB/s          48GB GDDR6
8xH100 / A6000      ~51×                 ~35× (单卡)       ~13×
```

实际同时间窗口内 A6000 训练能跑的 token 数大约是 8xH100 配置的 **1/30 到 1/50**。
对于参数量约 30M 的 tiny transformer：

```text
8xH100 在 10min 内大约能训：1.5-3B tokens（取决于 kernel 优化）
A6000 在 10min 内大约能训：40-100M tokens
```

含义：**直接套官方 10min cap 在 A6000 上等价于训练严重不足**。
val_bpb 不可能达到 1.22；初步预期 A6000 10min naive baseline 落在 1.7-2.2 范围。
具体数字必须实测，见 §6。

### 1.3 算力来源：本机 A6000 与云端等价

P3 anchor 的算力来源有两个互为等价替代的选项：

```text
本机 A6000
  成本：零边际（硬件成本已沉没）
  优势：低延迟、不受 pod 排队 / spot 抢占风险、长任务无中断
  劣势：单机受限，不能并行多 attempt
  适用：本机 A6000 可用时的默认首选

云端 A6000（如 Runpod）
  价格：~$0.49/hour 按需 (Secure)，~$0.44/hour Community Spot
  其他云商区间：$0.27 - $2.12/hour
  对照：1xH100 SXM5 on Runpod ~$2.69-$2.99/hour
  优势：弹性扩张、可并行多 pod、按秒计费、零硬件采购
  劣势：每次 pod 启动需拉数据/镜像；spot 有抢占风险；网络延迟
  适用：本机不可用时的等价替代；或需要并行多 session 时的临时扩容
```

P3 anchor 全程的云成本估算（仅当全程走云时）：

```text
M5 自主多 attempt loop（20 次 attempt × 10 min compute）
  ≈ 3.5 小时 × $0.49 ≈ €1.60
M5 完整验收（5 个完整 session 用于显著性）
  ≈ 17.5 小时 × $0.49 ≈ €8
P3 全程到 anchor 验收完成
  保守估 100-200 小时 ≈ €50-100
```

本机算力可用时该项成本为 €0；即便全程走云，绝对金额相对项目其他成本
（API token 消耗、时间）量级很小。结论：**本机 A6000 可用时为主算力源；
云端 A6000 作为等价备选与扩容选项**。两者在 train_gpt.py 层完全对称，
切换不需要改 anchor 配置。

### 1.4 A6000 的架构注意事项

```text
A6000 = NVIDIA Ampere（同 A100 一代）。
没有 FP8 tensor core（Hopper 独有）。
有 BF16 / FP16 / TF32 tensor cores，足以跑 leaderboard 上的大多数技巧
（Muon / BigramHash / GPTQ / int6 / QAT 全部支持）。

→ 要在 agent 的 system prompt 里明确：不要尝试 FP8 训练路径。
  如果某个 leaderboard PR 用 FP8（如 NeoMuon），需要先 fallback 到 BF16 再借鉴。
```

---

## 2. 三档算力方案

为不同阶段配不同算力，不要混用：

### Tier 1: Apple Silicon / MLX（dev / smoke）

```text
用途：agent loop 开发、schema 调试、Renderer 联调、Path 0-3 跑通
算力：Apple Silicon GPU（例 M4 Pro ~5 TFLOPS），远低于 A6000
脚本：train_gpt_mlx.py
配置：
  RUN_ID=mlx_smoke
  ITERATIONS=200-500
  TRAIN_BATCH_TOKENS=8192-16384
  VAL_LOSS_EVERY=0  # 跳过中途 val，只在最后跑一次
  --train-shards 1（单 shard，~100MB 数据）
单次 attempt：5-15 分钟
成本：免费
预期 val_bpb：不可比，仅验证 pipeline 跑通
关键点：不在这一档评估 agent 实验质量，只验证 plumbing。
```

### Tier 2: 单卡 A6000（本机或云，real experiments）

```text
用途：所有正式 attempt、M1-M5 验收、baseline 重建
算力：A6000 48GB，BF16 ~155 TFLOPS
脚本：train_gpt.py（torchrun --nproc_per_node=1）
配置：
  RUN_ID=<attempt_id>
  DATA_PATH=./data/datasets/fineweb10B_sp1024/
  TOKENIZER_PATH=./data/tokenizers/fineweb_1024_bpe.model
  VOCAB_SIZE=1024
  MAX_WALLCLOCK_SECONDS=480  # 8 分钟训练
  VAL_LOSS_EVERY=200          # 中途 val 取点画曲线
单次 attempt：8min 训练 + 1-2min eval ≈ 10min wall-clock
成本：本机 €0；云 ~€0.08/attempt
预期 val_bpb：naive baseline 在此预算下约 1.7-2.2（待实测）
关键点：所有真正的 metric 比较只在这一档进行。
```

### Tier 3: 单卡 H100（stretch / 加速复杂 attempt）

```text
用途：M5 后期、agent 提出需要更长训练才能验证的方案
脚本：与 Tier 2 同
配置：
  MAX_WALLCLOCK_SECONDS=600  # 提升到 10 分钟（接近官方 10min × 1/8 算力）
成本：~$0.45/attempt（vs A6000 €0.08）
关键点：不要默认用 H100。只有当 A6000 上某条线索明确受 token 不足限制
       且 agent 自己判断需要更多 compute 时才切。每次 H100 attempt 都要记录
       动机，避免悄无声息地把成本拉上去。
```

**Tier 选择规则**：dev 全部走 Tier 1；M1-M4 验收和 M5 主体走 Tier 2；
Tier 3 只作为 M5 中后期的 escalation 选项，需要 agent 明确请求 + 用户批准。

---

## 3. 单次 attempt 预算

固定到不能再变（跨 attempt metric 必须可比）：

```text
环境：
  Tier 2 配置（A6000 + sp1024 + --train-shards 1）

时间：
  MAX_WALLCLOCK_SECONDS=480       # 训练 8 分钟
  eval 自然结束（约 1-2 分钟）
  总计：~10 分钟 wall-clock

数据：
  train shards：1                  # 约 100M token，足够 8 分钟训练消化
  val：FineWeb val 前 50k docs（官方标准，~50M token，eval 一次约 1-2 分钟）
  如果 eval 时间过长，可改用 sliding window stride=64（详 README）

artifact 约束：
  16,000,000 bytes（保持官方约束，不缩小，否则失去与 leaderboard 经验的对应关系）

种子：
  固定随机种子，每个 attempt 必须可复现
  统计显著性测试时同种子至少跑 3 次（README 要求 p < 0.01，0.005 nat 改进阈值）

记录到 records/<attempt_id>/：
  train_gpt.py             # 实际跑的脚本（含本次 attempt 的 diff）
  submission.json          # 元数据（按 repo 约定）
  train_log.txt            # 自动产出
  README.md                # hypothesis / config diff / verdict（agent 自动生成）
```

---

## 4. Experiment session 预算

一个 session = agent 跑一棵 attempt 树。三档规模：

```text
Smoke session（验证 loop 跑得通，不评估 agent 质量）：
  attempts: 3-5
  tier: 1 或 2
  wall-clock: 30-60 分钟
  成本: €0 (本机) / €0.30-0.50 (云)
  用于: M1, M3, M4 验收

Standard session（验证 agent 自主闭环）：
  attempts: 15-25
  tier: 2
  wall-clock: 3-5 小时（夜间可挂着跑）
  成本: ~€2-3
  用于: M5 主体验收

Significance session（验证结果显著性）：
  同一 attempt 3-5 次重复
  tier: 2
  wall-clock: 30-60 分钟
  成本: ~€0.50
  用于: M5 最终验收（确认 "超过 baseline" 不是噪声）
```

停止条件：

```text
- 达到目标（agent 自主超过 A6000 naive baseline + 通过 significance session） → 成功停
- 连续 3 次 attempt metric 无显著改善 → agent 暂停，等用户介入
- session 预算耗尽（默认 5 小时或 30 attempts，取先到者）→ 停
- agent 试图升级到 Tier 3 → 停下来等用户批准
- 检测到任何越界（碰 val 数据 / artifact 超 16MB / 训练超时）→ 立即停
```

---

## 5. 重建 A6000 naive baseline（必须）

官方 naive baseline 的 1.2244 是 8xH100 10min 的数。**A6000 上必须重新建立 baseline**，
否则跨 tier 的 metric 不可比，agent 也没有合理的比较参照。

步骤：

```text
1. Tier 2 环境准备好（本机 A6000 或 Runpod A6000 pod）
2. 用 repo 自带 naive baseline 配置（9L 512d sp1024 TiedEmb 4KV）
3. MAX_WALLCLOCK_SECONDS=480 跑 5 次，每次不同种子
4. 记录 5 次 val_bpb 的均值与方差
5. 这个均值就是 P3 anchor 的 "A6000 naive baseline"
6. 显著性阈值：std × t-statistic（同 README 的 p<0.01 口径）
```

**这一步是 M0 的 hard prerequisite**。在 baseline 数字没拿到之前，
不能开始 M1。因为没有 baseline 就没有验收标准。

---

## 6. 第一次跑通的具体动作

把上面的所有抽象落到可复制粘贴的命令。假设你选 Runpod A6000 + 官方模板：

```bash
# 1. 起 pod，SSH 进去，落到 /workspace
cd /workspace
git clone https://github.com/openai/parameter-golf.git
cd parameter-golf

# 2. 下数据（默认 80 shards 8B tokens；只要 1 shard）
python3 data/cached_challenge_fineweb.py --variant sp1024 --train-shards 1

# 3. 跑 A6000 naive baseline（重复 5 次取均值）
for SEED in 42 43 44 45 46; do
  RUN_ID=a6000_naive_seed${SEED} \
  DATA_PATH=./data/datasets/fineweb10B_sp1024/ \
  TOKENIZER_PATH=./data/tokenizers/fineweb_1024_bpe.model \
  VOCAB_SIZE=1024 \
  MAX_WALLCLOCK_SECONDS=480 \
  SEED=${SEED} \
  torchrun --standalone --nproc_per_node=1 train_gpt.py 2>&1 | tee log_${SEED}.txt
done

# 4. 收集 5 次 val_bpb，算均值方差，作为 A6000 naive baseline
grep "val_bpb" log_*.txt
```

**预期产出**：5 个 val_bpb 数字 + 1 个均值 + 1 个 std。
这一步完成之后才能进 M0 验收。

Mac Mini M4 Pro 的 smoke 等价命令：

```bash
RUN_ID=mlx_smoke \
ITERATIONS=200 \
TRAIN_BATCH_TOKENS=8192 \
VAL_LOSS_EVERY=0 \
VAL_BATCH_SIZE=8192 \
python3 train_gpt_mlx.py
```

这条命令只用来验证 pipeline 通畅，不参与任何 metric 比较。

---

## 7. 限制与注意事项

```text
不能做的：
- 不能让 agent 改 dataset 或动 val（任何 "训练时碰 val" 都视为越界）
- 不能让 agent 任意延长 MAX_WALLCLOCK_SECONDS（这会破坏跨 attempt 可比性）
- 不能让 agent 默认升 Tier 3（H100 成本是 A6000 的 ~6 倍）
- 不能用 FP8 路径（A6000 不支持，会跑不起来或 silently 错）
- 不能让 artifact 超 16MB（Metric Harness 必须机器查死）

应该做的：
- 每次 attempt 必须产出 records/<attempt_id>/ 完整目录
- Trajectory（current best / last attempt / next action）走 P2 compaction
  deterministic fields，不另起一套
- 重要 attempt 自动跑 significance session（3 次重复）确认不是噪声
- agent system prompt 里写清楚 leaderboard 上哪些技巧已被验证（让 agent 站在
  公开 PR 的肩膀上而不是从零摸索）
```

---

## 8. 开放问题

```text
1. A6000 上 naive baseline 实测 val_bpb 究竟落在哪？目前估算 1.7-2.2，
   实测可能偏离这个区间。
2. eval 用全 50k docs 还是 sliding window 子采样？前者更标准，后者更快。
   M0 决策。
3. Runpod pod 的持久化存储成本（数据 + 8B token 全量缓存约 30GB）
   是否值得，还是每次 pod 重启重下？
4. agent 在 attempt 之间是否需要本地保留 records/？还是只把 manifest +
   verdict 上 Postgres、artifact bytes 用 workspace 文件即可？
5. 如果 Mac Mini 在 dev 阶段也想跑 CUDA 路径做对照（比如租 1 小时 A6000
   验证某次 MLX smoke 的结果），切换流程怎么走最顺？
6. session 之间的 attempt history 如何传给下一个 session？这是 P2 compaction
   deterministic fields 的天然测试场，但具体上下游协议需要在 architecture
   层确定。
```

---

## 一句话总结

```text
Mini Parameter Golf 的算力下沉到单卡 A6000 不需要重建框架——
用 repo 现成的 --train-shards 1 + MAX_WALLCLOCK_SECONDS=480，
本机 A6000 或 Runpod 云 A6000 (~€0.49/h) 都能跑，
P3 全程预算 €50-100，比卖 A6000 拿回的 €10,500 小两个数量级。
关键工作不是省算力，是重建 A6000 自己的 naive baseline 作为比较参照。
```
