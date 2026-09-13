# Official-protocol snapshot

This directory is a **live snapshot** of the official-harness rerun. Files here are incomplete until the corresponding job finishes. Judges have not been run yet.

See [`STATUS.json`](STATUS.json) for counts and [`../README.md`](../README.md) for protocol notes.

| Path | What it is |
|---|---|
| `swrbench/<model>/generation.jsonl` | Official SWR `base_review` rows (`instance_id`, `prompt`, `response`, `review`) |
| `martian/<model>/candidates.json` | `{pr_url: {review_comments: [{path, line, body}]}}` |
| `aacr/<model>/*.json` | OCR-schema result files for official `evaluate.py --reviewer ocr` |

Do not treat these as final scores.
