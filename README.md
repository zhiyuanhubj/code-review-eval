# Code Review Eval Trajectories

Single-shot code-review rollouts and scores for **Muse-Glimmer**, **Nemotron-3-Ultra**, **claude-opus-4-8**, and **gpt-5.6** on SWR-Bench, AACR-Bench, SWE-Review-Traj, and Martian.

This is **not** an agent / Harbor harness. Each model sees a truncated PR payload and must return JSON `{decision, findings}`. We score **decision vs `gold_clean`** only. That is a proxy, not official finding-match.

Dataset dumps are **not** in this repo (upstream licenses / size). Trajectories + scoring code are.

---

## 先看结论

1. **唯一比较干净的榜是 SWE-Review。** 这里 payload 里有 patch。Claude Opus 4.8 F1 **0.768**，GPT-5.6 **0.737**，都超过「永远 `request_changes`」的傻瓜基线 **0.676**。Nemotron **0.609**、Glimmer **0.396** 没有超过。
2. **SWR / AACR 上没有任何模型超过傻瓜基线。** SWR 永远提意见 F1=0.667，最好的 Nemotron 只有 **0.570**。AACR 傻瓜基线 0.825，Nemotron **0.787** 接近但仍低。原因不是「模型不会 review」，而是 **SWR 经常几乎没有可用 diff**，AACR 又被我们改成了一个别扭的二分类。
3. **Glimmer 的数字不能当 reviewer 分数用。** 100% 轨迹在回显评分 prompt（`to=selfYou are a senior code reviewer...`）。解析器会从 schema 示例里扫到 `"decision": "approve"`，所以它看起来特别「保守」。
4. **Martian 作废。** 没 local diff，四家全是 50/50 `approve`，F1=0。轨迹留着只为证明这一点。
5. 风格分裂很稳定：Glimmer / Opus 在 SWR+AACR 上偏 approve；Nemotron 偏乱提意见；到了 SWE-Review，Opus/GPT 反过来变成高召回。

DeepSeek-V4.1-Flash 还在跑，本仓库先不含它的分数。

---

## 仓库里有什么

```
run_openai_reviews.py   # 调 OpenAI-compatible 接口，写出 jsonl
score_cr_results.py     # 从 jsonl 重算 P/R/F1
results/scores.json     # 本 README 用的汇总
results/diagnostics.json
results/<model>_<bench>.jsonl
results/retries/        # 空输出补打的中间文件
```

每条轨迹一行 JSON：

| 字段 | 含义 |
|---|---|
| `instance_id` | 题目 id |
| `bench` | `swrbench` / `aacr` / `swe-review` / `martian` |
| `gold_clean` | `true` = 标签认为「干净 / 不应提意见」 |
| `model` | 模型名 |
| `latency_s` | 单条墙钟 |
| `output` | 模型原文（含 reasoning，若有） |
| `error` | 请求失败时的异常摘要 |

正类 = `decision == request_changes`。`gold_clean=true` 是否类。于是：

- TP：有问题的 PR，模型提了意见
- FN / **false approve (miss)**：有问题却 approve
- FP / **false reject (noise)**：干净却 request_changes
- TN：干净且 approve

---

## 主表（decision vs gold_clean）

傻瓜基线 = 全部 `request_changes`（SWR 金标正类率 50.0%，AACR 70.2%，SWE-Review 51.1%）。

### SWR-Bench（n=1000，平衡）

| Model | P | R | F1 | miss | noise | 提意见率 | vs 基线 0.667 |
|---|---:|---:|---:|---:|---:|---:|---|
| always `request_changes` | 0.50 | 1.00 | **0.667** | 0% | 100% | 100% | — |
| Nemotron-3-Ultra | 0.53 | 0.62 | 0.570 | 38.0% | 55.4% | 58.7% | 低于基线 |
| gpt-5.6 | 0.56 | 0.35 | 0.432 | 64.8% | 27.8% | 31.5% | 低于基线 |
| claude-opus-4-8 | 0.57 | 0.29 | 0.387 | 70.8% | 21.8% | 25.5% | 低于基线 |
| Muse-Glimmer | 0.52 | 0.14 | 0.226 | 85.6% | 13.2% | 13.8% | 不可比 |

混淆矩阵：Nemotron TP/FP/TN/FN = 310/277/223/190；Opus 146/109/391/354；GPT 176/139/361/324；Glimmer 72/66/434/428。

### AACR-Bench（n=2145，正类 70.2%）

这里的题目其实是「这条 review comment 是不是有效」，我们硬映射成 `request_changes`=有效、`approve`=噪声。**不是官方 AACR finding-match。**

| Model | P | R | F1 | miss | noise | 提意见率 | vs 基线 0.825 |
|---|---:|---:|---:|---:|---:|---:|---|
| always `request_changes` | 0.70 | 1.00 | **0.825** | 0% | 100% | 100% | — |
| Nemotron-3-Ultra | 0.72 | 0.87 | 0.787 | 12.8% | **80.8%** | 85.3% | 接近基线（几乎总是说有问题） |
| gpt-5.6 | 0.77 | 0.40 | 0.522 | 60.5% | 28.0% | 36.1% | 低于基线 |
| Muse-Glimmer | 0.72 | 0.29 | 0.411 | 71.2% | 26.6% | 28.1% | 不可比 |
| claude-opus-4-8 | 0.80 | 0.19 | 0.304 | 81.2% | 11.4% | 16.6% | 最保守 |

Nemotron 在 AACR 上 1830/2145 条都 `request_changes`。精确率和金标正类率差不多，召回高，噪声也高：它像一个「默认这条评论成立」的分类器，不是精细裁判。Opus 相反，只在 16.6% 的题上同意「这是真问题」，漏掉 81% 的正类。

### SWE-Review-Traj（n=8914，正类 51.1%）

`gold_clean = patch_resolved`。payload 里带 `patch`（截到 12k 字符）。这是四套里面最接近「看 diff 做审查」的。

| Model | P | R | F1 | miss | noise | 提意见率 | vs 基线 0.676 |
|---|---:|---:|---:|---:|---:|---:|---|
| claude-opus-4-8 | 0.66 | 0.91 | **0.768** | 9.0% | 48.3% | 70.2% | **超过基线** |
| gpt-5.6 | 0.62 | 0.92 | **0.737** | 8.4% | 59.5% | 75.9% | **超过基线** |
| always `request_changes` | 0.51 | 1.00 | 0.676 | 0% | 100% | 100% | — |
| Nemotron-3-Ultra | 0.83 | 0.48 | 0.609 | 52.0% | 10.0% | 29.3% | 低于基线 |
| Muse-Glimmer | 0.83 | 0.26 | 0.396 | 74.0% | 5.6% | 16.0% | 不可比 |

Opus：TP/FP/TN/FN = 4150/2105/2251/408。GPT：4175/2591/1764/383。两家几乎不漏未解决的 patch（miss ~9%），代价是把大约一半干净 patch 也打回。Nemotron 在这套上突然变得保守（提意见率 29%），精确率 0.83 是四家里最高的，但漏了一半真问题。

### Martian（n=50，作废）

四家全部 `approve`，F1=0。Prompt 只有 PR 标题和 URL，明确写了没 local diff。金标却全是「有问题」（`clean=False` 写死）。轨迹在 `*_martian.jsonl`，不要画进主图。

---

## 这些数字到底在说什么

### 1. 模型在两套「人格」之间切换

把 **提意见率** 摊开看：

| Model | SWR | AACR | SWE-Review |
|---|---:|---:|---:|
| Glimmer | 14% | 28% | 16% |
| Opus 4.8 | 26% | 17% | **70%** |
| GPT-5.6 | 32% | 36% | **76%** |
| Nemotron | **59%** | **85%** | 29% |

开源 Nemotron 在「短评论 / 残缺 diff」设定里爱找茬；有完整 patch 的 SWE-Review 上反而收着。闭源 Opus/GPT 正好反过来：SWR/AACR 很克制，SWE-Review 上变成高召回审查员。这更像 **输入里有没有可审的代码** 在驱动行为，而不是一条固定的「严格/宽松」人格。

### 2. SWR 的 diff 经常是空的

SWR 构造 payload 时只从 `pr_commits[*].diffs|files` 里各截 2000 字符。很多行这个字段对不上，模型实际只看到标题/描述。

输出里出现「没有 diff / 无法审查」这类话的比例：

| Model | SWR | AACR | SWE-Review |
|---|---:|---:|---:|
| Nemotron | **42.4%** | 0.2% | 0.6% |
| Opus 4.8 | 26.2% | 1.0% | 2.1% |
| GPT-5.6 | 17.8% | 0.1% | 0.3% |

Nemotron 在 SWR 上的一条 TP 长这样（`astropy__astropy-187`，金标是脏 PR）：

```json
{"decision":"request_changes","findings":[{"title":"Missing PR diff for review",
  "body":"The pull request description was provided but no code changes were included."}]}
```

同一题型的 FP（金标干净）也是同一句话。也就是说 **SWR 的 F1 很大一块在奖励/惩罚「会不会抱怨没 diff」**，不是在奖励找到真实 bug。GPT 较少抱怨（17.8%），F1 反而更低，因为它更常直接 `approve`。

在这种输入下，永远 `request_changes` 是很强的基线。没有模型打过它，是预期内的，不要解释成「Nemotron 比 Opus 更会做 code review」。

### 3. Glimmer：轨迹是 prompt 回显

`Muse-Glimmer_*` 里 **100%** 的 `output` 以 `to=selfYou are a senior code reviewer...` 开头，把评分 prompt 和 PR 原文又打了一遍。平均输出长度：SWR 3428 字符，AACR 8490，SWE-Review 8341（Nemotron 分别只有 298 / 500 / 575）。

`score_cr_results.py` 用正则取 **最后一次** `"decision": "approve"|"request_changes"`。Schema 示例里就有 `"decision": "approve"`，所以回显会被算成 approve。这正好对齐 Glimmer 13–28% 的提意见率和 74–86% 的 miss。

Glimmer jsonl 仍然上传了，方便复现这个故障；**不要拿它的 F1 去和另外三家比。**

### 4. AACR 是任务错位

官方 AACR 评的是「模型写的评论 vs 人类标注的有效/无效」。我们做的是：把已有评论塞进 prompt，让模型用 `request_changes` 表示「这是真问题」。Nemotron 85% 的时间说是，接近 70% 的基线率，所以 F1 好看。Opus 把大部分评论当成噪声（提意见率 17%），F1 最差。谁「更好」取决于你想要高召回过滤器还是高精确过滤器；当前 proxy **不能**回答「谁更会写 review」。

### 5. 模型两两同意率

同一条 `instance_id` 上 decision 是否一致：

| Pair | SWR | AACR | SWE-Review |
|---|---:|---:|---:|
| Opus vs GPT-5.6 | 0.69 | 0.70 | **0.80** |
| Glimmer vs Opus | 0.75 | 0.72 | 0.44 |
| Nemotron vs Opus | 0.50 | **0.31** | 0.57 |
| Nemotron vs GPT | 0.57 | 0.47 | 0.51 |
| Glimmer vs Nemotron | 0.48 | 0.41 | 0.75 |

Opus 和 GPT 在 SWE-Review 上最齐（都高召回）。Nemotron 和 Opus 在 AACR 上几乎反着来（0.31）：一个默认「评论成立」，一个默认「评论不成立」。Glimmer 和 Opus 在 SWR/AACR 上「一致」是因为两家都大量 approve，到 SWE-Review 就散了。

### 6. 延迟（墙钟，含网络）

| Model | SWR p50 | AACR p50 | SWE-Review p50 | SWE-Review mean |
|---|---:|---:|---:|---:|
| Opus 4.8 | 3.7s | 3.3s | 10.9s | 12.0s |
| Glimmer | 4.8s | 13.3s | 12.7s | 11.9s |
| Nemotron | 14.3s | 17.5s | 11.8s | 13.8s |
| GPT-5.6 (`reasoning_effort=high`) | 18.0s | 22.0s | 29.0s | **40.8s** |

GPT 明显更慢（SWE-Review p90=86s，max=382s），和 high reasoning 一致。Nemotron 空 `content` 时要靠 `reasoning_content` 拼出 JSON，所以比「短回答」看起来的更慢。解析失败率：SWR/AACR 四家都约 100%；SWE-Review 上 Glimmer 99.6%、Nemotron 99.7%、GPT 1 条空、Opus 全解析。

---

## 协议细节

- 接口：OpenAI-compatible `chat.completions`，**stream=true**。
- 输出约束：只要 JSON，`decision` ∈ {`approve`, `request_changes`}，外加 findings 列表（severity / category / path / line / title / body）。
- Prompt 截断：整段 payload **24000** 字符。SWR 最多 8 个 commit × 20 个 file × 2000 字符。SWE-Review patch 12000 字符。
- 温度：Glimmer/Nemotron 0.2；Claude / GPT-5.x 1.0。GPT-5.6 额外 `reasoning_effort=high`。
- `max_tokens=8192`。Nemotron 开了 `--reasoning-parser` 时，预算会先花在 think 上，runner 会把 `reasoning_content` 和 `content` 拼起来再解析。
- 空输出会按 `instance_id` 补打。`results/retries/` 是补打原始文件；主 jsonl 已经 merge 过。
- **不是** Harbor、不是多轮 agent、不跑测试、不对照 gold finding 文本。

数据集（需自行下载，不要指望本仓库）：

- SWR：`swr_datasets_d5c5.jsonl`，金标 `change_introduced`（有变更/引入问题 → 非 clean）
- AACR：`dataset.json`，金标 `label`（真值 → 非 clean）
- SWE-Review-Traj：HF parquet，金标 `patch_resolved` / `resolved`（已解决 → clean）
- Martian offline：只有标题，金标在本 harness 里无意义

---

## 复现打分

```bash
python3 score_cr_results.py --results ./results
```

不需要 GPU。产出打印表，并写 `results/updated_scores.json`。

重新跑模型（需要自己的 OpenAI-compatible endpoint）：

```bash
pip install openai
export OPENAI_API_KEY=dummy   # 本地 vLLM 可填 EMPTY
python3 run_openai_reviews.py \
  --base-url http://127.0.0.1:8000/v1 \
  --model Nemotron-3-Ultra \
  --bench swe-review \
  --data /path/to/swe-review-traj \
  --out results/Nemotron-3-Ultra_swe-review.jsonl \
  --workers 8 --max-tokens 8192
```

`--bench` ∈ `swrbench|aacr|swe-review|martian`。已成功的 `instance_id` 会跳过，所以可以断点续跑。

---

## 局限（读数字前请看完）

1. Binary decision proxy ≠ code-review 质量。Findings 的对错完全没评。
2. SWR payload 经常没有 diff；那一列 F1 被「有没有抱怨缺 diff」污染。
3. AACR 任务被改写了，不能和官方 leaderboard 比。
4. Glimmer 是 prompt echo，不是 reviewer。
5. Martian 没 diff。
6. 截断 24k / 12k 会切掉大 PR。
7. SWE-Review 的「未解决 patch」≠「patch 里一定有该提的 bug」；高召回可能只是模型看到不完整修复就打回。
8. 单次采样，温度对 Claude/GPT 是 1.0，所以有方差。
9. DeepSeek-V4.1-Flash 未完成，未进表。

---

## 建议怎么用这些轨迹

- 要比较「会不会看 patch 做审查」：只用 **SWE-Review**，并自己再做 finding-level 评测；本仓库的 F1 只是门禁。
- 要研究失败模式：SWR 里搜 `Missing PR diff` / `No implementation diff`；Glimmer 里搜 `to=self`。
- 不要把四套 bench 平均成一个总分。输入质量差了一个数量级。
- 若要重跑 SWR，先修 `load_swr()` 的 diff 字段，再谈模型高低。

---

## 许可

评分脚本 MIT。jsonl 是模型输出，供研究使用。上游 benchmark 文本/diff **没有** 放进本仓库，请走原项目许可。
