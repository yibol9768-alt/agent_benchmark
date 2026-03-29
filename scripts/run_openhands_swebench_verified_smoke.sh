#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/zsk8888/Desktop/lyb/agent_benchmark"
OPENHANDS_DIR="${OPENHANDS_DIR:-$ROOT_DIR/vendor/openhands-benchmarks}"
OUTPUT_DIR="${1:-$ROOT_DIR/dumps/openhands_verified_smoke}"
INSTANCE_ID="${2:-django__django-11333}"
MODEL_NAME="${3:-${GLM_MODEL:-glm-5}}"
DATASET="${DATASET:-princeton-nlp/SWE-bench_Verified}"
SPLIT="${SPLIT:-test}"
MAX_ITERATIONS="${MAX_ITERATIONS:-100}"
NUM_WORKERS="${NUM_WORKERS:-1}"
WORKSPACE="${WORKSPACE:-docker}"
NOTE="${NOTE:-glm_verified_smoke}"

if [ -z "${GLM_API_KEY:-}" ] || [ -z "${GLM_BASE_URL:-}" ]; then
  echo "GLM_API_KEY and GLM_BASE_URL must be exported"
  exit 1
fi

if [ ! -d "$OPENHANDS_DIR" ]; then
  echo "OpenHands benchmarks repo not found at $OPENHANDS_DIR"
  echo "Run: bash $ROOT_DIR/scripts/setup_openhands.sh"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

SELECT_FILE="$OUTPUT_DIR/instances.txt"
LLM_CONFIG_PATH="$OUTPUT_DIR/llm_config.json"
printf '%s\n' "$INSTANCE_ID" > "$SELECT_FILE"

if [[ "$MODEL_NAME" != */* ]]; then
  MODEL_NAME="openai/$MODEL_NAME"
fi

cat > "$LLM_CONFIG_PATH" <<EOF
{
  "model": "$MODEL_NAME",
  "base_url": "${GLM_BASE_URL%/}/",
  "api_key": "$GLM_API_KEY"
}
EOF

(
  cd "$OPENHANDS_DIR"
  uv run swebench-infer "$LLM_CONFIG_PATH" \
    --dataset "$DATASET" \
    --split "$SPLIT" \
    --select "$SELECT_FILE" \
    --workspace "$WORKSPACE" \
    --num-workers "$NUM_WORKERS" \
    --max-iterations "$MAX_ITERATIONS" \
    --output-dir "$OUTPUT_DIR" \
    --note "$NOTE"
)
