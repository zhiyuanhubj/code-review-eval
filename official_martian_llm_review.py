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


def _delta_dump(delta) -> dict:
    if delta is None:
        return {}
    if hasattr(delta, "model_dump"):
        try:
            return delta.model_dump(exclude_none=True) or {}
        except Exception:
            pass
    out = {}
    for key in ("content", "reasoning_content", "reasoning", "tool_calls"):
        val = getattr(delta, key, None)
        if val:
            out[key] = val
    extra = getattr(delta, "model_extra", None) or {}
    if extra:
        out.update(extra)
    return out


def _chunk_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or "")
    if isinstance(value, list):
        return "".join(_chunk_text(x) for x in value)
    return str(value)


def chat(base_url: str, api_key: str, model: str, prompt: str, max_tokens: int = 8192) -> str:
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
        "max_tokens": max_tokens,
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
        if not choice:
            continue
        dump = _delta_dump(getattr(choice, "delta", None))
        if dump.get("content"):
            content.append(_chunk_text(dump["content"]))
        for key in ("reasoning_content", "reasoning", "reasoning_details"):
            if dump.get(key):
                reason.append(_chunk_text(dump[key]))
    body = "".join(content).strip()
    thoughts = "".join(reason).strip()
    text = body or thoughts
    if text:
        return text
    # Stream captured nothing (common for muse_glimmer channel output). Non-stream fallback.
    kwargs["stream"] = False
    try:
        resp = cli.chat.completions.create(**kwargs)
    except Exception:
        kwargs["temperature"] = 1.0
        resp = cli.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    dump = _delta_dump(msg)
    return (
        _chunk_text(dump.get("content") or getattr(msg, "content", None)).strip()
        or _chunk_text(dump.get("reasoning_content") or getattr(msg, "reasoning_content", None)).strip()
        or _chunk_text(dump.get("reasoning")).strip()
    )


def parse_comments(text: str) -> list:
    if not text:
        return []
    t = text.strip()
    # Glimmer chat-template often wraps the answer as `to=self...to=user{json}`.
    if "to=user" in t:
        t = t.rsplit("to=user", 1)[-1]
    elif t.startswith("to=self"):
        t = t[len("to=self") :]
    decoder = json.JSONDecoder()
    blobs = []
    i = 0
    while i < len(t):
        j = t.find("{", i)
        if j < 0:
            break
        try:
            obj, end = decoder.raw_decode(t, j)
        except json.JSONDecodeError:
            i = j + 1
            continue
        if isinstance(obj, dict) and isinstance(obj.get("comments"), list):
            blobs.append(obj)
        i = max(end, j + 1)
    blob = blobs[-1] if blobs else None
    comments = blob.get("comments") if blob else None
    if not isinstance(comments, list):
        comments = _recover_truncated_comments(t)
    if not isinstance(comments, list):
        return []
    out = []
    placeholders = {"issue description", "file", "actionable review comment"}
    for item in comments:
        if not isinstance(item, dict):
            continue
        body = (item.get("body") or item.get("content") or item.get("comment") or "").strip()
        path = item.get("path") or item.get("file") or ""
        if not body or body in placeholders or path in placeholders:
            continue
        line = item.get("line") or item.get("start_line") or 0
        try:
            line = int(line)
        except (TypeError, ValueError):
            line = 0
        out.append({"path": path, "line": line, "body": body})
    return out


def _recover_truncated_comments(text: str) -> list:
    """Keep complete comment objects when the model cuts off mid-JSON."""
    marker = '"comments"'
    idx = text.find(marker)
    if idx < 0:
        return []
    bracket = text.find("[", idx)
    if bracket < 0:
        return []
    decoder = json.JSONDecoder()
    out = []
    i = bracket + 1
    while i < len(text):
        while i < len(text) and text[i] in " \n\r\t,":
            i += 1
        if i >= len(text) or text[i] == "]":
            break
        if text[i] != "{":
            break
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            out.append(obj)
        i = end
    return out


PROMPT = """You are a code reviewer. Review this pull request diff.
Put the JSON in the final answer, not in hidden thinking.
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
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--reparse-only", action="store_true", help="Re-parse existing raw_output; do not call the model")
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
    recovered = 0
    for url, rec in list(payload.items()):
        if not isinstance(rec, dict):
            continue
        if rec.get("review_comments"):
            continue
        comments = parse_comments(rec.get("raw_output") or "")
        if comments:
            rec["review_comments"] = comments
            recovered += 1
    if recovered:
        cand_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(json.dumps({"reparsed": recovered}), flush=True)
    if args.reparse_only:
        n_ok = sum(1 for v in payload.values() if isinstance(v, dict) and v.get("review_comments"))
        print(json.dumps({"wrote": str(cand_path), "with_comments": n_ok, "n": len(payload)}))
        return

    for i, (url, item) in enumerate(bench.items(), 1):
        if url in payload and payload[url].get("review_comments"):
            continue
        diff = fetch_pr_diff(url)
        text = chat(
            args.base_url,
            api_key,
            args.model,
            PROMPT.format(title=item.get("pr_title") or "", url=url, diff=diff),
            max_tokens=args.max_tokens,
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
        print(json.dumps({"progress": i, "n": len(bench), "url": url, "n_comments": len(comments), "raw_len": len(text or "")}), flush=True)
        time.sleep(0.2)
    print("wrote", cand_path)


if __name__ == "__main__":
    main()
