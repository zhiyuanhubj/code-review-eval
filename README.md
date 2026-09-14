# Code Review Eval Trajectories

Official-protocol rerun of **SWR-Bench**, **AACR-Bench**, **Martian offline**, and **SWE-Review-Bench (`glm5_500`)** for:

- DeepSeek-V4.1-Flash
- gpt-5.6
- Claude Opus 4.8 (`claude-opus-4-8`)
- Muse-Glimmer
- Nemotron-3-Ultra

This repo only contains the **official harness** rerun. Headline numbers: [`official/SCORES.json`](official/SCORES.json) (snapshot **2026-09-14 18:01 UTC**).

Judge for SWR / AACR / Martian: **gpt-5.6**. SWE-Review DA is the official `compute_da.py` (no extra LLM judge).

---

## Headline scores (official)

| Bench | DeepSeek-V4.1-Flash | gpt-5.6 | Claude Opus 4.8 | Muse-Glimmer | Nemotron-3-Ultra |
|---|---|---|---|---|---|
| **SWR** overall F1 (n=1000; Glimmer 990) | **0.666** | 0.654 | 0.576 | 0.542 | 0.422 |
| **AACR** semantic / line F1 (n=196) | **0.105 / 0.292** | 0.041 / 0.077 | 0.066 / 0.129 | 0.046 / 0.096 | 0.060 / 0.184 |
| **Martian** F1 (50 PRs) | 0 * | 21.4% | 37.8% | **40.8%** | 21.9% |
| **SWE-Review** DA (`glm5_500`) | 0 * | 52.0% | **74.9%** | 88.7% † | 70.9% |

\* DeepSeek-V4.1-Flash Martian / SWE-Review are **not usable** (empty generation / 500 env failures; not rerun).
† Glimmer SWE-Review DA is on **53 produced** reviews (CR 10.6%). Opus DA is on 327 (CR 65.4%). Same caveat as Nemotron (DA 70.9% on 79 produced, CR 15.8%).

---

## How we follow each benchmark

Datasets are **not** in this repo. Trajectories and scores are.

### SWR-Bench

Upstream: [`swrbench/generation.py`](https://github.com) `base_review` on `swr_datasets_d5c5.jsonl`, then `evaluation_struct.py`.

- Input is the official PR payload, including `pr_commits[].diff[].patch` (not a truncated title-only prompt).
- `max_tokens=8192`. Temperature **0.2** for local vLLM models.
- Claude / GPT-5.x chat APIs reject 0.2; we use **1.0** there (API constraint, not a protocol change of the prompt).
- gpt-5.6 also sends `reasoning_effort=high`.
- Glimmer is served with `vllm/vllm-openai:muse-glimmer` (`--tool-call-parser muse_glimmer --reasoning-parser muse_glimmer`).
- Metric reported here is **overall P/R/F1** from `evaluation_struct.py` (change-vs-clean plus finding match). Files: [`official/swrbench/<model>/generation.jsonl`](official/swrbench/) and [`metrics.json`](official/swrbench/DeepSeek-V4.1-Flash/metrics.json).

### AACR-Bench

Official dump used here is **196 PRs** (`dataset/positive_samples.json`). That is the PR-level OCR review set, not the 2145-row comment-classification table.

- Clone the repo, checkout `source_commit..target_commit`, ask the model for OCR-schema comments (`path`, `start_line`, `end_line`, `content`).
- Score with official `evaluation/evaluate.py --reviewer ocr` against human notes.
- Metrics: **semantic F1** and **line F1** (line match with `k=1`).
- This is an LLM reviewer in OCR schema, **not** the `@alibaba-group/open-code-review` CLI.
- DeepSeek-V4.1-Flash needed **thinking off**; otherwise the 4096-token budget was all hidden reasoning and the OCR file was empty.
- The official `judge.py` template had been hardcoded to Nemotron `chat_template_kwargs`; gpt-5.6 then returned HTTP 400 and F1 collapsed to 0. We removed that so every model is judged the same way.

Files: [`official/aacr/<model>/`](official/aacr/).

### Martian (offline)

Upstream: real GitHub PR file diffs, model comments `{path, line, body}`, then `step3_judge_comments.py` vs golden comments.

- We fetch PR files (`gh api …/pulls/{n}/files`) and give the model the diff, not a title-only prompt.
- Judge is gpt-5.6 at **temperature 1.0**. Temperature 0.0 is rejected by that API (HTTP 400), which previously zeroed every F1.
- `--no-dedup`, 50 PRs × 5 tools = 250 judge calls.
- **Valid scores:** Glimmer **40.8** F1 (45/50 PRs produced comments), Opus 37.8, Nemotron 21.9, gpt-5.6 21.4 (high precision, low recall).
- **DeepSeek-V4.1-Flash F1=0:** first generation wrote no comments (thinking ate the budget). Not rerun.
- Glimmer first-pass answers were often prompt-echo / hidden-channel CoT, and the original parser only accepted a single `json.loads` slice. We now use the AACR-style decoder (`to=user` split, raw_decode, truncated arrays), retry empty PRs, then rejudge **only** `Muse-Glimmer`.

Files: [`official/martian/<model>/candidates.json`](official/martian/) and [`official/martian/judge_input/results/gpt-5.6/evaluations.json`](official/martian/judge_input/results/gpt-5.6/evaluations.json).

### SWE-Review-Bench (`glm5_500`)

This is **SWE-Review-Bench**, not SWE-Review-Traj.

- Harbor + **OpenHands-SDK**, `OPENHANDS_LLM_NATIVE_TOOL_CALLING=true`.
- Split `glm5_500` (500 PRs). Decision-accuracy first (skip revision).
- Official metric is `compute_da.py`:
  - **DA** = correct approve/request_changes among trials that produced a parseable `review_report`
  - **CR** = produced / 500
  - **DA(total,50)** treats model failures as 0.5
- Opus: DA **74.9%**, CR 65.4% (327 produced). Nemotron: DA 70.9% but CR only 15.8% (79 produced, many docker failures). gpt-5.6: DA 52.0%, CR 25.0% (125 produced). Glimmer rerun: DA **88.7%** on 53 produced reviews (CR 10.6%).
- V4.1 first Harbor run produced **0** reports (agent/env failures) and is not being rerun.

Uploaded here: `da_metrics.json` plus `produced_reviews.jsonl` for models that wrote reports. Full Harbor docker trees are not in git.

---

## SWR detail

| Model | P | R | F1 | Acc | TP / FP / FN / TN |
|---|---:|---:|---:|---:|---|
| DeepSeek-V4.1-Flash | 0.547 | 0.852 | **0.666** | 0.573 | 426 / 353 / 74 / 147 |
| gpt-5.6 | 0.551 | 0.804 | 0.654 | 0.575 | 402 / 327 / 98 / 173 |
| Claude Opus 4.8 | 0.570 | 0.582 | 0.576 | 0.571 | 291 / 220 / 209 / 280 |
| Muse-Glimmer | 0.555 | 0.529 | 0.542 | 0.551 | 263 / 211 / 234 / 282 |
| Nemotron-3-Ultra | 0.618 | 0.320 | 0.422 | 0.561 | 160 / 99 / 340 / 401 |

## AACR detail (196 PRs, 1506 gold notes)

| Model | semantic F1 | line F1 | generated notes | semantic hits | line hits |
|---|---:|---:|---:|---:|---:|
| DeepSeek-V4.1-Flash | **0.105** | **0.292** | 679 | 115 | 320 |
| Nemotron-3-Ultra | 0.060 | 0.184 | 458 | 58 | 181 |
| Claude Opus 4.8 | 0.066 | 0.129 | 221 | 57 | 112 |
| Muse-Glimmer | 0.046 | 0.096 | 124 | 37 | 79 |
| gpt-5.6 | 0.041 | 0.077 | 94 | 33 | 62 |

## Martian detail (50 PRs)

| Model | P | R | F1 | Status |
|---|---:|---:|---:|---|
| Muse-Glimmer | 38.0% | 43.9% | **40.8%** | valid (45/50 PRs had comments) |
| Claude Opus 4.8 | 32.2% | 45.7% | 37.8% | valid |
| Nemotron-3-Ultra | 16.9% | 31.2% | 21.9% | valid |
| gpt-5.6 | 66.7% | 12.7% | 21.4% | valid |
| DeepSeek-V4.1-Flash | 0 | 0 | 0 | empty generation; not rerun |

## SWE-Review detail (`glm5_500`)

| Model | DA | DA(total,50) | CR | produced / 500 | TP / FP / TN / FN |
|---|---:|---:|---:|---:|---|
| Muse-Glimmer | 88.7% | 54.1% | 10.6% | 53 | 46 / 4 / 1 / 2 |
| Claude Opus 4.8 | **74.9%** | 66.3% | 65.4% | 327 | 216 / 65 / 29 / 17 |
| Nemotron-3-Ultra | 70.9% | 53.3% | 15.8% | 79 | 55 / 21 / 1 / 2 |
| gpt-5.6 | 52.0% | 50.5% | 25.0% | 125 | 39 / 11 / 26 / 49 |
| DeepSeek-V4.1-Flash | 0 | 50.0% | 0 | 0 | — |

---

## Repo layout

```
official/                      # trajectories and scores
official/SCORES.json           # headline metrics
official/swrbench/<model>/     # generation.jsonl + metrics.json
official/aacr/<model>/         # OCR reviews + evaluate.py metrics
official/martian/<model>/      # candidates.json + judge evaluations
official/swe-review/<model>/   # da_metrics.json + produced_reviews.jsonl
official_martian_llm_review.py
official_aacr_llm_reviewer.py
launch_official_swr.sh
```

Each SWR `generation.jsonl` row is one official `base_review` call (`instance_id`, prompt, response). AACR files are one OCR review per PR. Martian `candidates.json` is keyed by PR URL. SWE-Review `produced_reviews.jsonl` has `instance_id`, `decision`, and the `review_report` for trials that produced one.

---

## Limitations

1. DeepSeek-V4.1-Flash Martian and SWE-Review are **not** comparable (empty generation / 500 env failures; not rerun).
2. Glimmer and Nemotron SWE-Review DA are computed on 53 and 79 produced reviews (CR 10.6% / 15.8%). Opus CR is 65.4%.
3. AACR F1 is low for every model; that is the official finding-match task (semantic / line F1 against human notes), not a binary approve/reject score.
4. Claude/GPT temperature is 1.0 because those APIs reject 0.2.
5. We never put dataset dumps or Harbor sandbox trees in this repo.

---

## License

Scoring scripts are MIT. jsonl/json files are model outputs, intended for research. Upstream benchmark text/diffs are **not** in this repo; follow the original project licenses.
