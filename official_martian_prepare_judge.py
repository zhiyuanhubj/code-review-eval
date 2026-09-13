#!/usr/bin/env python3
"""Map our Martian candidate files onto the official step3_judge_comments.py layout.

Official judge reads:
  offline/results/{JUDGE_MODEL}/candidates.json[pr_url][tool] = [{text, path, line}]
and iterates tools from benchmark_data.json reviews[].

This writes a copy of benchmark_data with our reviewer tools injected, plus a
candidates.json the official judge can consume. Does not modify upstream files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

OFFLINE = Path("/fsx/home/zhiyuan/bench/code-review-eval/data/repos/martian-code-review-bench/offline")
OURS = Path("/fsx/home/zhiyuan/bench/code-review-eval/official/martian")
BENCH = OFFLINE / "results" / "benchmark_data.json"


def convert_one(src: dict, tool: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for url, rec in src.items():
        comments = rec.get("review_comments") or []
        items = []
        for c in comments:
            if not isinstance(c, dict):
                continue
            text = (c.get("body") or c.get("text") or c.get("content") or "").strip()
            if not text:
                continue
            items.append(
                {
                    "text": text,
                    "path": c.get("path"),
                    "line": c.get("line"),
                    "source": "llm_review",
                }
            )
        out[url] = {tool: items}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(OURS / "judge_input"))
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bench = json.loads(BENCH.read_text())
    merged_candidates: dict = {url: {} for url in bench}
    tools = []
    for cand_path in sorted(OURS.glob("*/candidates.json")):
        tool = cand_path.parent.name
        if tool in {"pr_files", "judge_input"}:
            continue
        src = json.loads(cand_path.read_text())
        converted = convert_one(src, tool)
        n = sum(1 for v in src.values() if isinstance(v, dict))
        tools.append((tool, n))
        for url, by_tool in converted.items():
            merged_candidates.setdefault(url, {}).update(by_tool)
        for url, rec in src.items():
            if url not in bench:
                continue
            reviews = bench[url].setdefault("reviews", [])
            if any(r.get("tool") == tool for r in reviews):
                continue
            reviews.append(
                {
                    "tool": tool,
                    "repo_name": bench[url].get("source_repo"),
                    "pr_url": url,
                    "review_comments": rec.get("review_comments") or [],
                }
            )
    (out / "benchmark_data.json").write_text(json.dumps(bench, indent=2, ensure_ascii=False))
    (out / "candidates.json").write_text(json.dumps(merged_candidates, indent=2, ensure_ascii=False))
    print(json.dumps({"out": str(out), "tools": tools}, indent=2))


if __name__ == "__main__":
    main()
