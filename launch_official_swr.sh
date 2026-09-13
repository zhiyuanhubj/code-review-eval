#!/usr/bin/env bash
# Official SWR-Bench protocol: swrbench/generation.py base_review (run.sh defaults)
# plus later evaluation_struct.py. Do not run on the login node.
set -euo pipefail
if [[ "$(hostname)" == ip-10-1-115-182 ]]; then
    echo "refusing official SWR client on the login node" >&2
    exit 2
fi

ROOT=/fsx/home/zhiyuan/bench/code-review-eval
SWR="$ROOT/data/repos/swrbench"
OUT="$ROOT/official/swrbench"
PY=/fsx/home/zhiyuan/miniconda3/envs/megatron-sft/bin/python
MODEL="${MODEL:?set MODEL}"
TAG="${TAG:-$MODEL}"
BASE_URL="${OPENAI_API_BASE:?set OPENAI_API_BASE}"
API_KEY="${OPENAI_API_KEY:-dummy}"
THREADS="${THREADS:-8}"
TEMPERATURE="${TEMPERATURE:-0.2}"
mkdir -p "$OUT/$TAG" /fsx/home/zhiyuan/logs/official-cr

export OPENAI_API_BASE="$BASE_URL"
export OPENAI_API_KEY="$API_KEY"
export PYTHONPATH="$SWR/swrbench:${PYTHONPATH:-}"
export SWR_STREAM="${SWR_STREAM:-1}"
if [[ -n "${SWR_EXTRA_BODY_JSON:-}" ]]; then
    export SWR_EXTRA_BODY_JSON
fi

cd "$SWR"
"$PY" -m pip install -q loguru tenacity tqdm openai python-dateutil
echo "[official-swr $(date -u '+%F %T UTC')] model=$MODEL tag=$TAG threads=$THREADS temperature=$TEMPERATURE host=$(hostname)"
exec "$PY" swrbench/generation.py \
    --dataset-file "$SWR/data/swr_datasets_d5c5.jsonl" \
    --model "$MODEL" \
    --max-tokens 8192 \
    --temperature "$TEMPERATURE" \
    --num-threads "$THREADS" \
    --output-file "$OUT/$TAG/generation.jsonl"
