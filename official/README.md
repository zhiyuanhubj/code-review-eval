# Official trajectories

This tree is the **official-harness** rerun. Cite [`SCORES.json`](SCORES.json), not the old proxy jsonl under `../results/`.

| Path | What it is |
|---|---|
| `SCORES.json` / `STATUS.json` | Headline metrics and protocol snapshot |
| `swrbench/<model>/generation.jsonl` | Official SWR `base_review` rows |
| `swrbench/<model>/metrics.json` | `evaluation_struct.py` overall P/R/F1 (full per-item dump omitted; too large for GitHub) |
| `aacr/<model>/*.json` | OCR-schema reviews for `evaluate.py --reviewer ocr` |
| `aacr/<model>/metrics_ocr_*.json` | Official AACR summary (semantic / line F1) |
| `martian/<model>/candidates.json` | `{pr_url: {review_comments: [{path, line, body}]}}` |
| `martian/judge_input/results/gpt-5.6/evaluations.json` | Official Martian LLM-as-judge |
| `swe-review/<model>/da_metrics.json` | Official `compute_da.py` |
| `swe-review/<model>/produced_reviews.jsonl` | Harbor trials that wrote a `review_report` (Opus / gpt-5.6 / Nemotron only) |

We do **not** upload Harbor docker trees, verifier logs, or dataset dumps.
