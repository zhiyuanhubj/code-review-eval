#!/usr/bin/env bash
# Continue official-protocol CR evals. Orchestrate from login via srun.
# Glimmer vLLM is READY on 2087:31001. Restart Claude/GPT SWR at temperature=1.0
# because those APIs reject the official 0.2.
set -uo pipefail
ROOT=/fsx/home/zhiyuan/bench
CR=$ROOT/code-review-eval
LOG=/fsx/home/zhiyuan/logs/official-cr
RELAY_KEY=$(tr -d '\r\n' < "$ROOT/model-relay-token.txt")
PROXY=http://10.1.115.182:8102/v1
PY=/fsx/home/zhiyuan/miniconda3/envs/megatron-sft/bin/python
mkdir -p "$LOG"
chmod +x "$CR/launch_official_swr.sh"

bg() {
  local name=$1
  shift
  echo "[official] start $name"
  nohup "$@" >"$LOG/${name}.log" 2>&1 &
  disown
  echo "[official] pid $! log $LOG/${name}.log"
}

echo "[official] restart Claude/GPT SWR at temperature=1.0 (API constraint); keep in-progress rows"
srun --jobid=2087 --overlap -N1 -n1 -w ip-10-1-48-227 --time=2 --mem=0 bash -lc \
  'pkill -f "official/swrbench/claude-opus-4-8/generation.jsonl" || true
   pkill -f "official/swrbench/gpt-5.6/generation.jsonl" || true
   echo killed_swr_opus_gpt' \
  || true
sleep 2

bg swr_opus srun --jobid=2087 --overlap -N1 -n1 -w ip-10-1-48-227 --time=12:00:00 --mem=16G \
  env MODEL=claude-opus-4-8 TAG=claude-opus-4-8 TEMPERATURE=1.0 \
      OPENAI_API_BASE=$PROXY OPENAI_API_KEY=$RELAY_KEY THREADS=3 \
  bash "$CR/launch_official_swr.sh"

bg swr_gpt srun --jobid=2087 --overlap -N1 -n1 -w ip-10-1-48-227 --time=12:00:00 --mem=16G \
  env MODEL=gpt-5.6 TAG=gpt-5.6 TEMPERATURE=1.0 \
      OPENAI_API_BASE=$PROXY OPENAI_API_KEY=$RELAY_KEY THREADS=3 \
      SWR_EXTRA_BODY_JSON='{"reasoning_effort":"high"}' \
  bash "$CR/launch_official_swr.sh"

bg swr_glimmer srun --jobid=2087 --overlap -N1 -n1 -w ip-10-1-48-227 --time=12:00:00 --mem=16G \
  env MODEL=Muse-Glimmer TAG=Muse-Glimmer TEMPERATURE=0.2 \
      OPENAI_API_BASE=http://127.0.0.1:31001/v1 OPENAI_API_KEY=EMPTY THREADS=4 \
  bash "$CR/launch_official_swr.sh"

bg martian_glimmer srun --jobid=2087 --overlap -N1 -n1 -w ip-10-1-48-227 --time=06:00:00 --mem=8G \
  bash -lc "
    export OPENAI_API_KEY=EMPTY
    $PY $CR/official_martian_llm_review.py --model Muse-Glimmer --tool-name Muse-Glimmer \
      --base-url http://127.0.0.1:31001/v1 --out-dir $CR/official/martian/Muse-Glimmer
  "

bg aacr_glimmer srun --jobid=2087 --overlap -N1 -n1 -w ip-10-1-48-227 --time=18:00:00 --mem=16G \
  bash -lc "
    export OPENAI_API_KEY=EMPTY
    export AACR_REPO_DIR=/opt/dlami/nvme/zhiyuan-official-aacr/repos
    mkdir -p \$AACR_REPO_DIR
    $PY $CR/official_aacr_llm_reviewer.py --model Muse-Glimmer \
      --base-url http://127.0.0.1:31001/v1 \
      --out-dir $CR/official/aacr/Muse-Glimmer \
      --workers 1 --max-tokens 4096
  "

bg aacr_v41 srun --jobid=2086 --overlap -N1 -n1 -w ip-10-1-25-253 --time=18:00:00 --mem=16G \
  bash -lc "
    export OPENAI_API_KEY=EMPTY
    export AACR_REPO_DIR=/opt/dlami/nvme/zhiyuan-official-aacr/repos
    mkdir -p \$AACR_REPO_DIR
    $PY $CR/official_aacr_llm_reviewer.py --model DeepSeek-V4.1-Flash \
      --base-url http://127.0.0.1:30210/v1 \
      --out-dir $CR/official/aacr/DeepSeek-V4.1-Flash \
      --workers 1 --max-tokens 4096
  "

echo "[official] continue launched $(date -u '+%F %T UTC')"
