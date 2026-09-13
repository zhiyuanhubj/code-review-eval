#!/usr/bin/env python3
"""Generate Martian-offline reviews from the real PR diff, then official-judge them.

Official Martian scoring is LLM-as-judge vs golden comments (step3_judge_comments.py).
This script:
  1. pulls the original PR files via `gh api`
  2. asks the model for path/line/body comments
  3. writes results/<tool>/candidates.json in the official candidate schema
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from openai import OpenAI

ROOT = Path("/fsx/home/zhiyuan/bench/code-review-eval/data/repos/martian-code-review-bench/offline")
BENCH = ROOT / "results" / "benchmark_data.json"
GOLDEN = ROOT / "golden_comments"


def pr_parts(url: str) -> tuple[str, str, str]:
    path = urlparse(url).path.strip("/").split("/")
    # owner/repo/pull/N
    return path[0], path[1], path[3]


def fetch_pr_diff(url: str) -> str:
    cache_dir = Path("/fsx/home/zhiyuan/bench/code-review-eval/official/martian/pr_files")
    cache = cache_dir / (url.replace("https://", "").replace("/", "_") + ".txt")
    if cache.exists() and cache.stat().st_size > 20:
        return cache.read_text()[:80000]
    owner, repo, number = pr_parts(url)
    gh_bin = "gh"
    for cand in ("/usr/bin/gh", os.path.expanduser("~/.local/bin/gh"), "gh"):
        if cand == "gh" or os.path.isfile(cand):
            gh_bin = cand
            break
    proc = subprocess.run(
        [gh_bin, "api", f"repos/{owner}/{repo}/pulls/{number}/files", "--paginate"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return f"(failed to fetch diff: {proc.stderr[:200]})"
    try:
        files = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout[:20000]
    chunks = []
    for item in files[:40]:
        filename = item.get("filename") or ""
        patch = item.get("patch") or ""
        chunks.append(f"FILE {filename}\n{patch}")
    return "\n\n".join(chunks)[:80000]


def chat(base_url: str, api_key: str, model: str, prompt: str) -> str:
    cli = OpenAI(
        base_url=base_url.rstrip("/") + ("" if base_url.rstrip("/").endswith("/v1") else "/v1"),
        api_key=api_key,
        timeout=600.0,
    )
    extra = {}
    raw = os.environ.get("SWR_EXTRA_BODY_JSON", "").strip()
    if raw:
        extra["extra_body"] = json.loads(raw)
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.2,
        "stream": True,
        **extra,
    }
    content, reason = [], []
    try:
        stream = cli.chat.completions.create(**kwargs)
    except Exception:
        kwargs["temperature"] = 1.0
        stream = cli.chat.completions.create(**kwargs)
    for event in stream:
        choice = (event.choices or [None])[0]
        if not choice or not choice.delta:
            continue
        if choice.delta.content:
            content.append(choice.delta.content)
        extra_d = getattr(choice.delta, "model_extra", None) or {}
        r = getattr(choice.delta, "reasoning_content", None) or extra_d.get("reasoning_content")
        if r:
            reason.append(str(r))
    return "\n".join(p for p in ("".join(reason).strip(), "".join(content).strip()) if p)


def parse_comments(text: str) -> list:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        blob = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    comments = blob.get("comments") if isinstance(blob, dict) else None
    if not isinstance(comments, list):
        return []
    out = []
    for item in comments:
        if not isinstance(item, dict):
            continue
        body = (item.get("body") or item.get("content") or item.get("comment") or "").strip()
        if not body:
            continue
        out.append(
            {
                "path": item.get("path") or "",
                "line": item.get("line") or 0,
                "body": body,
            }
        )
    return out


PROMPT = """You are a code reviewer. Review this pull request diff.
Return ONLY JSON:
{{"comments":[{{"path":"file","line":1,"body":"issue description"}}]}}
If there are no real issues, return {{"comments":[]}}.

Title: {title}
URL: {url}

Diff:
{diff}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--tool-name", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    api_key = os.environ.get("OPENAI_API_KEY", "dummy")
    bench = json.loads(BENCH.read_text())
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cand_path = out_dir / "candidates.json"
    existing = {}
    if cand_path.exists():
        existing = json.loads(cand_path.read_text())

    payload = existing if isinstance(existing, dict) else {}
    for i, (url, item) in enumerate(bench.items(), 1):
        if url in payload and payload[url].get("review_comments"):
            continue
        diff = fetch_pr_diff(url)
        text = chat(
            args.base_url,
            api_key,
            args.model,
            PROMPT.format(title=item.get("pr_title") or "", url=url, diff=diff),
        )
        comments = parse_comments(text)
        payload[url] = {
            "tool": args.tool_name,
            "pr_url": url,
            "pr_title": item.get("pr_title"),
            "review_comments": comments,
            "raw_output": text[:15000],
            "latency_s": None,
        }
        cand_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(json.dumps({"progress": i, "n": len(bench), "url": url, "n_comments": len(comments)}), flush=True)
        time.sleep(0.2)
    print("wrote", cand_path)


if __name__ == "__main__":
    main()
