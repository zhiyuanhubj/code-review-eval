#!/usr/bin/env python3
"""Generate code-review comments via an OpenAI-compatible endpoint.

Runs on a compute node. Writes one JSONL per (model, bench) under --out-dir.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI


REVIEW_PROMPT = """You are a senior code reviewer. Review the following pull request.
Return ONLY a JSON object with this schema:
{{
  "decision": "approve" | "request_changes",
  "findings": [
    {{
      "severity": "critical" | "high" | "medium" | "low",
      "category": "bug" | "security" | "logic" | "test" | "style" | "other",
      "path": "file path or empty",
      "line": 0,
      "title": "short title",
      "body": "actionable comment"
    }}
  ]
}}
If the PR is clean, return decision=approve and findings=[].
Do not invent issues. Prefer precision over recall for nits; still report real bugs.

PR:
{payload}
"""


def client_for(base_url: str) -> OpenAI:
    return OpenAI(
        base_url=base_url.rstrip("/") + ("" if base_url.rstrip("/").endswith("/v1") else "/v1"),
        api_key=os.environ.get("OPENAI_API_KEY", "dummy"),
        timeout=600.0,
        max_retries=2,
    )


def _message_text(msg) -> str:
    """Join visible content and Nemotron reasoning_content.

    With --reasoning-parser nemotron_v3, a too-small max_tokens budget is spent
    on thinking and message.content is empty even though the call succeeded.
    """
    content = getattr(msg, "content", None) or ""
    reasoning = getattr(msg, "reasoning_content", None) or ""
    extra = getattr(msg, "model_extra", None) or {}
    if not reasoning and isinstance(extra, dict):
        reasoning = extra.get("reasoning_content") or extra.get("reasoning") or ""
    if isinstance(reasoning, dict):
        reasoning = reasoning.get("content") or reasoning.get("text") or ""
    parts = [str(x).strip() for x in (reasoning, content) if x]
    return "\n".join(parts).strip()


def chat(
    cli: OpenAI,
    model: str,
    payload: str,
    max_tokens: int,
    extra_body: dict | None = None,
    temperature: float | None = 0.2,
) -> str:
    # Stream so Cloudflare in front of the relay does not 502 a buffered wait.
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": REVIEW_PROMPT.format(payload=payload[:24000])}],
        "max_tokens": max_tokens,
        "timeout": 600.0,
        "stream": True,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if extra_body:
        kwargs["extra_body"] = extra_body
    content_parts: list[str] = []
    reason_parts: list[str] = []
    stream = cli.chat.completions.create(**kwargs)
    for event in stream:
        choice = (event.choices or [None])[0]
        if choice is None:
            continue
        delta = choice.delta
        if delta is None:
            continue
        if delta.content:
            content_parts.append(delta.content)
        extra = getattr(delta, "model_extra", None) or {}
        reasoning = getattr(delta, "reasoning_content", None) or extra.get("reasoning_content") or extra.get("reasoning")
        if reasoning:
            reason_parts.append(str(reasoning))
    parts = ["".join(reason_parts).strip(), "".join(content_parts).strip()]
    return "\n".join(p for p in parts if p)


def load_swr(path: Path, limit: int) -> list[dict]:
    rows = []
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            commits = item.get("pr_commits") or []
            diffs = []
            for commit in commits[:8]:
                for diff in (commit.get("diffs") or commit.get("files") or [])[:20]:
                    diffs.append(str(diff)[:2000])
            payload = (
                f"repo={item.get('repo')} title={item.get('pr_title')}\n"
                f"{item.get('pr_statement') or ''}\n"
                + "\n".join(diffs)
            )
            rows.append({"instance_id": item.get("instance_id"), "bench": "swrbench",
                         "clean": not item.get("change_introduced"), "payload": payload})
            if limit and len(rows) >= limit:
                break
    return rows


def load_martian(golden_dir: Path, limit: int) -> list[dict]:
    rows = []
    bench_json = golden_dir / "results" / "benchmark_data.json"
    if not bench_json.exists() and golden_dir.name != "offline":
        cand = golden_dir.parent / "results" / "benchmark_data.json"
        if cand.exists():
            bench_json = cand
        elif (golden_dir / "benchmark_data.json").exists():
            bench_json = golden_dir / "benchmark_data.json"
    if bench_json.exists():
        data = json.loads(bench_json.read_text())
        items = data.items() if isinstance(data, dict) else enumerate(data)
        for key, item in items:
            if not isinstance(item, dict):
                continue
            url = str(key) if not isinstance(key, int) else item.get("original_url") or item.get("url") or ""
            title = item.get("pr_title") or ""
            # Do not leak golden_comments / az_comment into the prompt.
            payload = (
                f"PR url: {url}\n"
                f"title: {title}\n"
                f"source_repo: {item.get('source_repo') or ''}\n"
                "No local diff is available; infer likely defects from the title and repo context. "
                "If you cannot justify a finding, return approve with findings=[]."
            )
            rows.append({"instance_id": url or title, "bench": "martian", "clean": False, "payload": payload})
            if limit and len(rows) >= limit:
                return rows
        return rows
    for path in sorted(golden_dir.glob("*.json")):
        data = json.loads(path.read_text())
        items = data if isinstance(data, list) else list(data.values())
        for item in items:
            if not isinstance(item, dict):
                continue
            pr = item.get("pr") or item.get("url") or item.get("html_url") or ""
            title = item.get("title") or item.get("pr_title") or ""
            payload = f"PR url: {pr}\ntitle: {title}\nIf you cannot justify a finding, return approve with findings=[]."
            rows.append({"instance_id": f"{path.stem}:{title[:40]}", "bench": "martian",
                         "clean": False, "payload": payload})
            if limit and len(rows) >= limit:
                return rows
    return rows


def load_aacr(path: Path, limit: int) -> list[dict]:
    """AACR items are review-comment quality labels, not generate-a-review prompts."""
    rows = []
    if path.is_dir():
        files = [p for p in list(path.rglob("dataset.json")) + list(path.rglob("*.jsonl")) + list(path.rglob("*.json"))
                 if ".cache" not in p.parts]
        path = files[0] if files else path
    if not path.exists():
        return rows
    if path.suffix == ".jsonl":
        blobs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    else:
        data = json.loads(path.read_text())
        blobs = data if isinstance(data, list) else [data]
    for idx, item in enumerate(blobs):
        note = item.get("note") or ""
        payload = (
            "Decide whether this code-review comment is valid and actionable for the PR.\n"
            "Return JSON with decision=request_changes if the comment is a real issue, "
            "or decision=approve if the comment is noise/wrong/not actionable.\n"
            f"language={item.get('project_main_language')} pr={item.get('pr_url')}\n"
            f"path={item.get('path')} lines={item.get('from_line')}-{item.get('to_line')} "
            f"claimed_category={item.get('category')} context={item.get('context')}\n"
            f"comment:\n{note}"
        )
        rows.append({"instance_id": f"{item.get('pr_url')}|{item.get('path')}|{item.get('from_line')}|{idx}",
                     "bench": "aacr",
                     "clean": not bool(item.get("label")),
                     "payload": payload,
                     "gold_label": item.get("label")})
        if limit and len(rows) >= limit:
            break
    return rows


def _swe_item_to_row(item: dict) -> dict:
    patch = item.get("model_patch") or item.get("patch") or ""
    payload = (
        f"repo={item.get('repo')}\n"
        f"title={item.get('pr_title') or ''}\n"
        f"issue={item.get('problem_statement') or ''}\n"
        f"pr_body={item.get('pr_body') or ''}\n"
        f"patch:\n{patch[:12000]}"
    )
    iid = item.get("pr_instance_id") or item.get("instance_id") or item.get("issue_instance_id")
    resolved = item.get("patch_resolved")
    if resolved is None:
        resolved = item.get("resolved")
    return {"instance_id": iid, "bench": "swe-review", "clean": bool(resolved), "payload": payload}


def load_swe_review(path: Path, limit: int) -> list[dict]:
    rows = []
    files = [path] if path.is_file() else [
        p for p in list(path.rglob("*.jsonl")) + list(path.rglob("*.parquet"))
        if ".cache" not in p.parts
    ]
    files = sorted(files)
    for file in files:
        if file.suffix == ".jsonl":
            for line in file.read_text().splitlines():
                if not line.strip():
                    continue
                rows.append(_swe_item_to_row(json.loads(line)))
                if limit and len(rows) >= limit:
                    return rows
        elif file.suffix == ".parquet":
            import pandas as pd
            df = pd.read_parquet(file)
            for rec in df.to_dict(orient="records"):
                rows.append(_swe_item_to_row(rec))
                if limit and len(rows) >= limit:
                    return rows
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--bench", required=True, choices=["swrbench", "martian", "aacr", "swe-review"])
    parser.add_argument("--data", required=True)
    parser.add_argument("--limit", type=int, default=0, help="0 = all rows")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--temperature", type=float, default=None,
                        help="sampling temperature; default 0.2, or 1.0 for claude-*")
    parser.add_argument("--reasoning-effort", default="",
                        help="pass reasoning_effort for gpt-5.x via extra_body")
    parser.add_argument("--shard", default="0/1", help="i/n md5 split so two replicas do not overlap")
    parser.add_argument("--only-ids", default="", help="file of instance_id values to keep, one per line")
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="ask Nemotron/vLLM not to spend the token budget on a think block",
    )
    args = parser.parse_args()

    data = Path(args.data)
    if args.bench == "swrbench":
        rows = load_swr(data, args.limit)
    elif args.bench == "martian":
        rows = load_martian(data, args.limit)
    elif args.bench == "aacr":
        rows = load_aacr(data, args.limit)
    else:
        rows = load_swe_review(data, args.limit)
    shard_i, shard_n = (int(x) for x in args.shard.split("/", 1))
    if shard_n > 1:
        rows = [
            row for row in rows
            if int(hashlib.md5(str(row["instance_id"]).encode()).hexdigest(), 16) % shard_n == shard_i
        ]
    if args.only_ids:
        keep = {line.strip() for line in Path(args.only_ids).read_text().splitlines() if line.strip()}
        rows = [row for row in rows if str(row["instance_id"]) in keep]
    extra_body: dict | None = None
    if args.no_thinking:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    if args.reasoning_effort:
        extra_body = dict(extra_body or {})
        extra_body["reasoning_effort"] = args.reasoning_effort
    model_l = args.model.lower()
    if args.temperature is not None:
        temperature = args.temperature
    elif "claude" in model_l:
        temperature = 1.0
    elif model_l.startswith("gpt-5"):
        temperature = 1.0
    else:
        temperature = 0.2
    print(json.dumps({"bench": args.bench, "n": len(rows), "model": args.model,
                      "base_url": args.base_url, "shard": args.shard,
                      "only_ids": bool(args.only_ids), "no_thinking": args.no_thinking,
                      "max_tokens": args.max_tokens, "temperature": temperature,
                      "reasoning_effort": args.reasoning_effort or None}), flush=True)
    if not rows:
        raise SystemExit("no rows loaded")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("error") or not (rec.get("output") or "").strip():
                continue
            done.add(rec.get("instance_id"))

    def work(row: dict) -> dict:
        t0 = time.time()
        try:
            text = chat(
                client_for(args.base_url),
                args.model,
                row["payload"],
                args.max_tokens,
                extra_body=extra_body,
                temperature=temperature,
            )
            err = None
        except Exception as exc:
            text, err = "", f"{type(exc).__name__}: {exc}"[:400]
        return {
            "instance_id": row["instance_id"],
            "bench": row["bench"],
            "gold_clean": row["clean"],
            "model": args.model,
            "latency_s": round(time.time() - t0, 2),
            "output": text,
            "error": err,
        }

    pending = [row for row in rows if row["instance_id"] not in done]
    print(json.dumps({"pending": len(pending), "already_done": len(done)}), flush=True)
    with out.open("a") as handle, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(work, row) for row in pending]
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            line = json.dumps(rec, ensure_ascii=False) + "\n"
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line)
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            if i % 5 == 0 or i == len(futs):
                print(f"progress {i}/{len(futs)} err={rec.get('error') is not None}", flush=True)


if __name__ == "__main__":
    main()
