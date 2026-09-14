#!/usr/bin/env python3
"""Official AACR evaluation protocol with an OpenAI-compatible LLM reviewer.

Uses AACR's repo checkout (base..head) and writes OCR-schema result files so
evaluation/evaluate.py can score comments against gold with the official judge.
This is the LLM-reviewer setting (OCR-style findings), not Claude Code / Codex.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

EVAL_DIR = Path("/fsx/home/zhiyuan/bench/code-review-eval/data/repos/aacr-bench/evaluation")
sys.path.insert(0, str(EVAL_DIR))

from converters.aacr_bench import convert_record  # noqa: E402
from repo_utils import clean_worktree, prepare_repo  # noqa: E402
from schema import ReviewInstance  # noqa: E402
import config  # noqa: E402

REVIEW_PROMPT = """You are reviewing a GitHub pull request with full repository context.
Return ONLY JSON:
{{
  "comments": [
    {{
      "path": "repo-relative file path",
      "start_line": 1,
      "end_line": 1,
      "content": "actionable review comment"
    }}
  ]
}}
If the PR looks good, return {{"comments": []}}.
Do not invent issues. Prefer precision over recall for nits; still report real bugs.

PR: {repo} {instance_id}
Base: {base}
Head: {head}

Diff (truncated):
{diff}
"""


def git_diff(repo_path: Path, base: str, head: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "diff", f"{base}...{head}"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    return (proc.stdout or "")[:120000]


def chat(base_url: str, api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    cli = OpenAI(base_url=base_url.rstrip("/") + ("" if base_url.rstrip("/").endswith("/v1") else "/v1"),
                 api_key=api_key, timeout=600.0)
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
        if not choice or not choice.delta:
            continue
        if choice.delta.content:
            content.append(choice.delta.content)
        extra_d = getattr(choice.delta, "model_extra", None) or {}
        r = (
            getattr(choice.delta, "reasoning_content", None)
            or extra_d.get("reasoning_content")
            or getattr(choice.delta, "reasoning", None)
            or extra_d.get("reasoning")
        )
        if r:
            reason.append(str(r))
    # Prefer the visible answer. Thinking-only streams are a fallback.
    visible = "".join(content).strip()
    thought = "".join(reason).strip()
    return visible or thought


def parse_comments(text: str) -> list:
    if not text:
        return []
    t = text.strip()
    # Glimmer chat-template often echoes the prompt as `to=self...to=user{json}`.
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
    if blob is None:
        return []
    comments = blob.get("comments")
    out = []
    placeholders = {"actionable review comment", "repo-relative file path"}
    for item in comments:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or item.get("body") or item.get("note") or "").strip()
        path = item.get("path") or item.get("file") or ""
        if not content or content in placeholders or path in placeholders:
            continue
        out.append(
            {
                "path": path,
                "start_line": item.get("start_line") or item.get("line") or 1,
                "end_line": item.get("end_line") or item.get("line") or item.get("start_line") or 1,
                "content": content,
                "side": "right",
            }
        )
    return out


def load_instances(limit: int) -> list[ReviewInstance]:
    src = Path("/fsx/home/zhiyuan/bench/code-review-eval/data/repos/aacr-bench/dataset/positive_samples.json")
    data = json.loads(src.read_text())
    rows = []
    for item in data:
        inst = convert_record(item)
        if inst is not None:
            rows.append(inst)
        if limit and len(rows) >= limit:
            break
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=4096)
    args = ap.parse_args()
    api_key = os.environ.get("OPENAI_API_KEY", "dummy")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    repo_dir = Path(os.environ.get("AACR_REPO_DIR", "/opt/dlami/nvme/zhiyuan-official-aacr/repos"))
    repo_dir.mkdir(parents=True, exist_ok=True)
    instances = load_instances(args.limit)
    print(json.dumps({"n": len(instances), "model": args.model, "workers": args.workers}), flush=True)
    repo_locks: dict[str, threading.Lock] = {}
    repo_locks_guard = threading.Lock()

    def lock_for(repo_name: str) -> threading.Lock:
        with repo_locks_guard:
            return repo_locks.setdefault(repo_name, threading.Lock())

    def work(inst: ReviewInstance) -> dict:
        dest = config.result_path(out, inst.instance_id)
        if dest.exists():
            return {"instance_id": inst.instance_id, "status": "skip"}
        t0 = time.time()
        repo_path = None
        try:
            with lock_for(inst.repo):
                repo_path = prepare_repo(
                    repo_dir=repo_dir,
                    clone_url=inst.resolved_clone_url,
                    repo_full_name=inst.repo,
                    base_commit=inst.base_commit,
                    head_commit=inst.head_commit,
                )
                diff = git_diff(repo_path, inst.base_commit, inst.head_commit)
            prompt = REVIEW_PROMPT.format(
                repo=inst.repo,
                instance_id=inst.instance_id,
                base=inst.base_commit,
                head=inst.head_commit,
                diff=diff or "(empty diff)",
            )
            text = chat(args.base_url, api_key, args.model, prompt, args.max_tokens)
            comments = parse_comments(text)
            err = None
        except Exception as exc:
            comments, text, err = [], "", f"{type(exc).__name__}: {exc}"[:400]
            repo_path = None
        payload = {
            "instance_id": inst.instance_id,
            "repo": inst.repo,
            "base_commit": inst.base_commit,
            "head_commit": inst.head_commit,
            "reviewer": "ocr",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.time() - t0, 2),
            "ocr_exit_code": 0 if not err else 1,
            "ocr_format": "json",
            "review": {"comments": comments},
            "raw_output": (text or "")[:20000],
            "stderr": err,
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        if repo_path:
            try:
                with lock_for(inst.repo):
                    clean_worktree(repo_path)
            except Exception:
                pass
        return {"instance_id": inst.instance_id, "status": "ok" if not err else "error", "n_comments": len(comments)}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(work, inst) for inst in instances]
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            if i % 5 == 0 or i == len(futs):
                print(json.dumps({"progress": i, "total": len(futs), **rec}), flush=True)


if __name__ == "__main__":
    main()
