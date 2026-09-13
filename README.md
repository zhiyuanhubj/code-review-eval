# Code Review Eval Trajectories

Official-protocol rerun is **in progress**. The tables below the fold are the **old unofficial proxy** (single-shot chat, truncated payloads, decision-vs-`gold_clean` only). Do not cite those numbers as SWR / AACR / SWE-Review / Martian scores.

## Official protocol (running now)

| Bench | Official setting we are now using | Status |
|---|---|---|
| **SWR-Bench** | Upstream `swrbench/generation.py` **base_review** (full `pr_commits[].diff[].patch`, temperature 0.2 / 1.0 if the API rejects 0.2) then `evaluation_struct.py` LLM-as-judge (PR-level + point-level) | Generating: Nemotron, V4.1-Flash, Opus 4.8, GPT-5.6. Glimmer weights downloaded; vLLM coming up. Judge not started yet. |
| **AACR-Bench** | 200 PRs, `git clone` + checkout `source_commit..target_commit`, emit OCR-schema comments, official `evaluate.py` / `judge.py` finding match | Nemotron reviewing cloned repos. |
| **Martian offline** | Real GitHub PR file diffs + comments `{path,line,body}`, then official `step3_judge_comments.py` vs golden comments | Generating candidates for Nemotron / V4.1 / Opus / GPT-5.6 (50 PRs). |
| **SWE-Review-Bench** | Harbor + OpenHands-SDK agentic review on `glm5_500` (500 PRs), decision accuracy. Not SWE-Review-Traj. | Task generation started for Nemotron; `--skip-revision` DA first. |

Claude/GPT-5 APIs only accept `temperature=1`; we keep the official SWR prompt and only bump temperature when the provider 400s.

New trajectories will land under `official/` in this repo and replace the headline tables once the official judges finish.

---

## Legacy unofficial proxy (do not cite)

Single-shot code-review rollouts and scores for **Muse-Glimmer**, **Nemotron-3-Ultra**, **claude-opus-4-8**, and **gpt-5.6** on SWR-Bench, AACR-Bench, SWE-Review-Traj, and Martian.

This was **not** an agent / Harbor harness. Each model saw a truncated PR payload and returned JSON `{decision, findings}`. We scored **decision vs `gold_clean` only**. That is a proxy, not official finding-match.

Dataset dumps are **not** in this repo (upstream licenses / size). Trajectories and scoring code are.

---

## Legacy takeaways (unofficial proxy only)

1. **SWE-Review is the only relatively clean leaderboard.** The payload includes a patch. Claude Opus 4.8 F1 is **0.768** and GPT-5.6 is **0.737**, both above the trivial always-`request_changes` baseline of **0.676**. Nemotron (**0.609**) and Glimmer (**0.396**) are not.
2. **No model beats that trivial baseline on SWR or AACR.** Always requesting changes scores 0.667 F1 on SWR; the best model (Nemotron) is **0.570**. On AACR the dummy scores 0.825; Nemotron is **0.787**. This is not because the models cannot review. **SWR often has no usable diff**, and AACR was rewritten into an awkward binary task.
3. **Do not treat Glimmer numbers as reviewer scores.** 100% of its trajectories echo the scoring prompt (`to=selfYou are a senior code reviewer...`). The parser then picks `"decision": "approve"` out of the schema example, so Glimmer looks artificially conservative.
4. **Martian is invalid.** There is no local diff. All four models approved all 50 rows (F1 = 0). The files are kept only to document that.
5. The style split is stable: Glimmer / Opus lean `approve` on SWR+AACR; Nemotron over-flags; on SWE-Review, Opus/GPT flip into high-recall reviewers.

---

## Repo layout

```
run_openai_reviews.py   # OpenAI-compatible client; writes jsonl
score_cr_results.py     # recompute P/R/F1 from jsonl
results/scores.json     # snapshot used by this README
results/diagnostics.json
results/<model>_<bench>.jsonl
results/retries/        # extra calls that filled empty outputs
```

Each trajectory is one JSON object:

| Field | Meaning |
|---|---|
| `instance_id` | example id |
| `bench` | `swrbench` / `aacr` / `swe-review` / `martian` |
| `gold_clean` | `true` = label says clean / should not request changes |
| `model` | model name |
| `latency_s` | wall clock per example |
| `output` | raw model text (including reasoning, if any) |
| `error` | short exception if the request failed |

Positive class = `decision == request_changes`. `gold_clean=true` is the negative class.

- TP: buggy / unresolved item, model requested changes
- FN / **false approve (miss)**: should have requested changes, approved instead
- FP / **false reject (noise)**: clean item, requested changes
- TN: clean and approved

---

## Main table (decision vs `gold_clean`)

The dummy baseline is always `request_changes` (gold positive rates: SWR 50.0%, AACR 70.2%, SWE-Review 51.1%).

### SWR-Bench (n=1000, balanced)

| Model | P | R | F1 | miss | noise | request rate | vs baseline 0.667 |
|---|---:|---:|---:|---:|---:|---:|---|
| always `request_changes` | 0.50 | 1.00 | **0.667** | 0% | 100% | 100% | — |
| Nemotron-3-Ultra | 0.53 | 0.62 | 0.570 | 38.0% | 55.4% | 58.7% | below |
| gpt-5.6 | 0.56 | 0.35 | 0.432 | 64.8% | 27.8% | 31.5% | below |
| claude-opus-4-8 | 0.57 | 0.29 | 0.387 | 70.8% | 21.8% | 25.5% | below |
| Muse-Glimmer | 0.52 | 0.14 | 0.226 | 85.6% | 13.2% | 13.8% | not comparable |

Confusion matrices (TP/FP/TN/FN): Nemotron 310/277/223/190; Opus 146/109/391/354; GPT 176/139/361/324; Glimmer 72/66/434/428.

### AACR-Bench (n=2145, 70.2% positive)

The underlying items are “is this review comment valid?”. We mapped valid → `request_changes` and noise → `approve`. **This is not official AACR finding-match.**

| Model | P | R | F1 | miss | noise | request rate | vs baseline 0.825 |
|---|---:|---:|---:|---:|---:|---:|---|
| always `request_changes` | 0.70 | 1.00 | **0.825** | 0% | 100% | 100% | — |
| Nemotron-3-Ultra | 0.72 | 0.87 | 0.787 | 12.8% | **80.8%** | 85.3% | near dummy (almost always flags) |
| gpt-5.6 | 0.77 | 0.40 | 0.522 | 60.5% | 28.0% | 36.1% | below |
| Muse-Glimmer | 0.72 | 0.29 | 0.411 | 71.2% | 26.6% | 28.1% | not comparable |
| claude-opus-4-8 | 0.80 | 0.19 | 0.304 | 81.2% | 11.4% | 16.6% | most conservative |

Nemotron requested changes on 1830/2145 AACR rows. Precision matches the base rate; recall is high; noise is high. It behaves like “assume the comment is real,” not a careful judge. Opus agrees that the comment is a real issue on only 16.6% of rows and misses 81% of positives.

### SWE-Review-Traj (n=8914, 51.1% positive)

`gold_clean = patch_resolved`. The payload includes `patch` (truncated to 12k chars). This is the closest of the four to “read a diff and review it.”

| Model | P | R | F1 | miss | noise | request rate | vs baseline 0.676 |
|---|---:|---:|---:|---:|---:|---:|---|
| claude-opus-4-8 | 0.66 | 0.91 | **0.768** | 9.0% | 48.3% | 70.2% | **above** |
| gpt-5.6 | 0.62 | 0.92 | **0.737** | 8.4% | 59.5% | 75.9% | **above** |
| always `request_changes` | 0.51 | 1.00 | 0.676 | 0% | 100% | 100% | — |
| Nemotron-3-Ultra | 0.83 | 0.48 | 0.609 | 52.0% | 10.0% | 29.3% | below |
| Muse-Glimmer | 0.83 | 0.26 | 0.396 | 74.0% | 5.6% | 16.0% | not comparable |

Opus TP/FP/TN/FN = 4150/2105/2251/408. GPT = 4175/2591/1764/383. Both barely miss unresolved patches (~9% miss) and bounce about half of the clean ones. Nemotron becomes conservative here (29% request rate): highest precision of the four (0.83), but it misses half of the real issues.

### Martian (n=50, invalid)

All four models approved every row, F1 = 0. The prompt only had a PR title and URL and said there was no local diff. Gold is hardcoded `clean=False`. Files: `*_martian.jsonl`. Do not plot this bench.

---

## What the numbers actually say

### 1. Models switch “personality” with the input

Request-change rate:

| Model | SWR | AACR | SWE-Review |
|---|---:|---:|---:|
| Glimmer | 14% | 28% | 16% |
| Opus 4.8 | 26% | 17% | **70%** |
| GPT-5.6 | 32% | 36% | **76%** |
| Nemotron | **59%** | **85%** | 29% |

Open Nemotron nitpicks when the input is a short comment or a missing diff, then holds back when a full patch is present. Closed Opus/GPT do the opposite: restrained on SWR/AACR, high-recall reviewers on SWE-Review. That looks like **whether there is reviewable code in the prompt**, not a fixed strict/lenient personality.

### 2. SWR diffs are often missing

SWR payloads are built from `pr_commits[*].diffs|files`, 2000 chars each. On many rows those fields do not match, so the model only sees title/description.

Share of outputs that talk about a missing diff / inability to review:

| Model | SWR | AACR | SWE-Review |
|---|---:|---:|---:|
| Nemotron | **42.4%** | 0.2% | 0.6% |
| Opus 4.8 | 26.2% | 1.0% | 2.1% |
| GPT-5.6 | 17.8% | 0.1% | 0.3% |

A Nemotron SWR true positive (`astropy__astropy-187`, gold dirty):

```json
{"decision":"request_changes","findings":[{"title":"Missing PR diff for review",
  "body":"The pull request description was provided but no code changes were included."}]}
```

False positives on gold-clean rows say the same thing. A large slice of SWR F1 is **rewarding or punishing “complained about a missing diff”**, not finding real bugs. GPT complains less (17.8%) and scores lower F1 because it more often just approves.

Under this input, always-`request_changes` is a strong baseline. No model beating it is expected. Do not read it as “Nemotron is better at code review than Opus.”

### 3. Glimmer trajectories are prompt echoes

Every `Muse-Glimmer_*` `output` starts with `to=selfYou are a senior code reviewer...` and dumps the scoring prompt plus the PR. Mean output length: SWR 3428 chars, AACR 8490, SWE-Review 8341 (Nemotron: 298 / 500 / 575).

`score_cr_results.py` takes the **last** `"decision": "approve"|"request_changes"` match. The schema example contains `"decision": "approve"`, so an echo is scored as approve. That matches Glimmer’s 13–28% request rate and 74–86% miss.

The Glimmer jsonl is still here so the failure is reproducible. **Do not compare its F1 to the other three.**

### 4. AACR is the wrong task

Official AACR scores model-written comments against human valid/invalid labels. Here we stuffed an existing comment into the prompt and asked the model to use `request_changes` for “this is a real issue.” Nemotron says yes 85% of the time, near the 70% base rate, so F1 looks good. Opus treats most comments as noise (17% request rate) and gets the worst F1. Which is “better” depends on whether you want a high-recall filter or a high-precision filter. This proxy **cannot** answer “who writes better reviews.”

### 5. Pairwise decision agreement

Same `instance_id`, same binary decision:

| Pair | SWR | AACR | SWE-Review |
|---|---:|---:|---:|
| Opus vs GPT-5.6 | 0.69 | 0.70 | **0.80** |
| Glimmer vs Opus | 0.75 | 0.72 | 0.44 |
| Nemotron vs Opus | 0.50 | **0.31** | 0.57 |
| Nemotron vs GPT | 0.57 | 0.47 | 0.51 |
| Glimmer vs Nemotron | 0.48 | 0.41 | 0.75 |

Opus and GPT are most aligned on SWE-Review (both high recall). Nemotron and Opus almost invert on AACR (0.31): one defaults to “the comment is valid,” the other to “it is not.” Glimmer vs Opus looks high on SWR/AACR only because both approve a lot; they diverge on SWE-Review.

### 6. Latency (wall clock, including network)

| Model | SWR p50 | AACR p50 | SWE-Review p50 | SWE-Review mean |
|---|---:|---:|---:|---:|
| Opus 4.8 | 3.7s | 3.3s | 10.9s | 12.0s |
| Glimmer | 4.8s | 13.3s | 12.7s | 11.9s |
| Nemotron | 14.3s | 17.5s | 11.8s | 13.8s |
| GPT-5.6 (`reasoning_effort=high`) | 18.0s | 22.0s | 29.0s | **40.8s** |

GPT is clearly slower (SWE-Review p90 = 86s, max = 382s), consistent with high reasoning. Nemotron often spends the token budget on think and leaves `content` empty; the runner concatenates `reasoning_content` + `content`, so it is slower than the short final JSON suggests. Parse rates: ~100% on SWR/AACR for all four; on SWE-Review, Glimmer 99.6%, Nemotron 99.7%, GPT one empty row, Opus fully parsed.

---

## Protocol

- API: OpenAI-compatible `chat.completions`, **stream=true**.
- Output: JSON only. `decision` ∈ {`approve`, `request_changes`} plus a findings list (`severity` / `category` / `path` / `line` / `title` / `body`).
- Truncation: full payload **24000** chars. SWR: up to 8 commits × 20 files × 2000 chars. SWE-Review patch: 12000 chars.
- Temperature: Glimmer/Nemotron 0.2; Claude / GPT-5.x 1.0. GPT-5.6 also uses `reasoning_effort=high`.
- `max_tokens=8192`. With Nemotron `--reasoning-parser`, the budget is spent on thinking first; the runner concatenates `reasoning_content` and `content` before parsing.
- Empty outputs were retried by `instance_id`. `results/retries/` holds the raw retry files; the main jsonl files are already merged.
- **Not** Harbor, not a multi-turn agent, no tests executed, no gold-finding text match.

Datasets (download yourself; they are not in this repo):

- SWR: `swr_datasets_d5c5.jsonl`, gold `change_introduced` (introduced a change/bug → not clean)
- AACR: `dataset.json`, gold `label` (truthy → not clean)
- SWE-Review-Traj: HF parquet, gold `patch_resolved` / `resolved` (resolved → clean)
- Martian offline: title only; gold is meaningless in this harness

---

## Recompute scores

```bash
python3 score_cr_results.py --results ./results
```

No GPU. Prints a table and writes `results/updated_scores.json`.

Rerun a model (needs your own OpenAI-compatible endpoint):

```bash
pip install openai
export OPENAI_API_KEY=dummy   # EMPTY is fine for local vLLM
python3 run_openai_reviews.py \
  --base-url http://127.0.0.1:8000/v1 \
  --model Nemotron-3-Ultra \
  --bench swe-review \
  --data /path/to/swe-review-traj \
  --out results/Nemotron-3-Ultra_swe-review.jsonl \
  --workers 8 --max-tokens 8192
```

`--bench` ∈ `swrbench|aacr|swe-review|martian`. Successful `instance_id`s are skipped, so runs are resume-safe.

---

## Limitations

1. Binary decision proxy ≠ code-review quality. Finding correctness is not scored.
2. SWR payloads often have no diff; that column’s F1 is contaminated by “complained about missing diff.”
3. AACR was rewritten; do not compare to the official leaderboard.
4. Glimmer is prompt echo, not a reviewer.
5. Martian has no diff.
6. 24k / 12k truncation cuts large PRs.
7. An unresolved SWE-Review patch is not the same as “this patch contains a bug worth flagging.” High recall may just mean the model bounces incomplete fixes.
8. Single sample. Claude/GPT temperature is 1.0, so there is variance.
9. DeepSeek-V4.1-Flash was incomplete and is not in the table.

---

## How to use these trajectories

- To compare “can this model review a patch”: use **SWE-Review only**, and add your own finding-level eval. F1 here is only a gate.
- For failure modes: search SWR for `Missing PR diff` / `No implementation diff`; search Glimmer for `to=self`.
- Do not average the four benches into one score. Input quality differs by an order of magnitude.
- If you rerun SWR, fix the diff fields in `load_swr()` before ranking models.

---

## License

Scoring scripts are MIT. jsonl files are model outputs, intended for research. Upstream benchmark text/diffs are **not** in this repo; follow the original project licenses.
