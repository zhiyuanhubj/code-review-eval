Canonical merged rollouts live in this directory as `<model>_<bench>.jsonl`.

`retries/` holds the extra calls that filled empty `output` rows (Nemotron SWE-Review / SWR / AACR, GPT-5.6 SWE-Review). The main files already include those replacements.

`scores.json` is the snapshot used by the top-level README. Recompute with:

```
python3 score_cr_results.py --results ./results
```

Martian files are included only to document that the bench is invalid in this harness (no local diff).
