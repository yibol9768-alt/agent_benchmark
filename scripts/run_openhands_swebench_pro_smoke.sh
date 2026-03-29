#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/zsk8888/Desktop/lyb/agent_benchmark"
OUTPUT_ROOT="${1:-$ROOT_DIR/dumps/openhands_swebench_pro_smoke}"
INSTANCE_ID="${2:-instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan}"
MODEL_NAME="${3:-${GLM_MODEL:-glm-5}}"
MAX_ITERATIONS="${MAX_ITERATIONS:-100}"
DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME:-jefzda}"
PYTHON_BIN="$ROOT_DIR/vendor/openhands-benchmarks/.venv/bin/python"

if [ -z "${GLM_API_KEY:-}" ] || [ -z "${GLM_BASE_URL:-}" ]; then
  echo "GLM_API_KEY and GLM_BASE_URL must be exported"
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "OpenHands virtualenv not found at $PYTHON_BIN"
  echo "Run: bash $ROOT_DIR/scripts/setup_openhands.sh"
  exit 1
fi

"$PYTHON_BIN" "$ROOT_DIR/benchmark_suite/run_openhands_swebench_pro.py" \
  --output-root "$OUTPUT_ROOT" \
  --split test \
  --instance-id "$INSTANCE_ID" \
  --model "$MODEL_NAME" \
  --base-url "$GLM_BASE_URL" \
  --api-key "$GLM_API_KEY" \
  --max-iterations "$MAX_ITERATIONS" \
  --dockerhub-username "$DOCKERHUB_USERNAME" \
  --prefix "openhands_pro_smoke"
