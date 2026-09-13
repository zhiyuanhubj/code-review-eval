#!/usr/bin/env python3
"""Decision-vs-gold_clean proxy scores for code-review jsonl outputs."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

DECISION_RE = re.compile(
    r'"decision"\s*:\s*"(approve|request_changes)"',
    re.IGNORECASE,
)
LOOSE_REQ = re.compile(r"\brequest[_\s-]?changes\b", re.IGNORECASE)
LOOSE_APP = re.compile(r"\bapprove\b", re.IGNORECASE)


def extract_decision(text: str | None) -> str | None:
    if not text or not str(text).strip():
        return None
    matches = list(DECISION_RE.finditer(text))
    if matches:
        return matches[-1].group(1).lower()
    if LOOSE_REQ.search(text):
        return "request_changes"
    if LOOSE_APP.search(text):
        return "approve"
    return None


def load_jsonl(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not path.exists():
        return rows
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = rec.get("instance_id")
            if iid is None:
                continue
            rows[str(iid)] = rec
    return rows


def merge_retries(orig: Path, retry_paths: list[Path], dest: Path) -> dict:
    rows = load_jsonl(orig)
    n_orig = len(rows)
    replaced = 0
    added = 0
    for retry in retry_paths:
        for iid, rec in load_jsonl(retry).items():
            if iid in rows:
                replaced += 1
            else:
                added += 1
            rows[iid] = rec
    backup = orig.with_suffix(".pre_retry.jsonl")
    if orig.exists() and not backup.exists():
        shutil.copy2(orig, backup)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w") as handle:
        for rec in rows.values():
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
    empty = sum(1 for rec in rows.values() if not (rec.get("output") or "").strip())
    return {
        "n": len(rows),
        "n_orig": n_orig,
        "replaced": replaced,
        "added": added,
        "empty_after": empty,
        "dest": str(dest),
    }


def score(path: Path) -> dict:
    rows = list(load_jsonl(path).values())
    n = len(rows)
    parsed = 0
    tp = fp = tn = fn = 0
    errors = 0
    empty = 0
    for rec in rows:
        if rec.get("error"):
            errors += 1
        text = rec.get("output") or ""
        if not str(text).strip():
            empty += 1
        pred = extract_decision(text)
        gold_clean = bool(rec.get("gold_clean"))
        if pred is None:
            continue
        parsed += 1
        pred_pos = pred == "request_changes"
        gold_pos = not gold_clean
        if pred_pos and gold_pos:
            tp += 1
        elif pred_pos and not gold_pos:
            fp += 1
        elif (not pred_pos) and (not gold_pos):
            tn += 1
        else:
            fn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    reca = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * reca / (prec + reca) if (prec + reca) else 0.0
    miss = fn / (fn + tp) if (fn + tp) else 0.0
    noise = fp / (fp + tn) if (fp + tn) else 0.0
    return {
        "n": n,
        "parsed": parsed,
        "parse_pct": round(100.0 * parsed / n, 1) if n else 0.0,
        "empty": empty,
        "errors": errors,
        "precision": round(prec, 3),
        "recall": round(reca, 3),
        "f1": round(f1, 3),
        "false_approve_miss": round(100.0 * miss, 1),
        "false_reject_noise": round(100.0 * noise, 1),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
    }


def fmt_row(name: str, s: dict) -> str:
    return (
        f"| {name} | {s['n']} | {s['parse_pct']}% | {s['precision']:.2f} | "
        f"{s['recall']:.2f} | {s['f1']:.2f} | {s['false_approve_miss']}% | "
        f"{s['false_reject_noise']}% |"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--results",
        default="./results",
    )
    ap.add_argument("--merge-swe-retry", action="store_true")
    args = ap.parse_args()
    results = Path(args.results)
    merge_info = None
    if args.merge_swe_retry:
        orig = results / "Nemotron-3-Ultra_swe-review.jsonl"
        merge_info = merge_retries(
            orig,
            [
                results / "Nemotron-3-Ultra_swe-review.retry0.jsonl",
                results / "Nemotron-3-Ultra_swe-review.retry1.jsonl",
            ],
            orig,
        )

    benches = [
        ("Martian", "martian"),
        ("SWR", "swrbench"),
        ("AACR", "aacr"),
        ("SWE-Review", "swe-review"),
    ]
    models = [
        ("Glimmer", "Muse-Glimmer"),
        ("Nemotron-3-Ultra", "Nemotron-3-Ultra"),
        ("claude-opus-4-8", "claude-opus-4-8"),
        ("gpt-5.6", "gpt-5.6"),
        ("DeepSeek-V4.1-Flash", "DeepSeek-V4.1-Flash"),
    ]
    table = [
        "| Bench | Model | N | Parse | P | R | F1 | False approve (miss) | False reject (noise) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    payload = {"merge": merge_info, "scores": {}}
    for bench_name, suffix in benches:
        payload["scores"][bench_name] = {}
        for model_name, stem in models:
            path = results / f"{stem}_{suffix}.jsonl"
            if not path.exists():
                continue
            s = score(path)
            if s["n"] == 0:
                continue
            payload["scores"][bench_name][model_name] = s
            table.append(fmt_row(f"{bench_name} / {model_name}", s))

    out_json = results / "updated_scores.json"
    out_json.write_text(json.dumps(payload, indent=2) + "\n")
    print("\n".join(table))
    if merge_info:
        print(json.dumps({"swe_merge": merge_info}))
    print(f"wrote {out_json}")


if __name__ == "__main__":
    main()
