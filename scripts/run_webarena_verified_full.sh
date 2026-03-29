#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
OUTPUT_DIR="${1:-$ROOT_DIR/dumps/webarena_verified_full}"
MODEL_NAME="${2:-${GLM_MODEL:-glm-5}}"
CONFIG="${CONFIG:-$ROOT_DIR/configs/webarena/webarena_config.json}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Project venv not found. Run: cd $ROOT_DIR && uv sync"
  exit 1
fi

if [ -z "${GLM_API_KEY:-}" ] || [ -z "${GLM_BASE_URL:-}" ]; then
  echo "GLM_API_KEY and GLM_BASE_URL must be exported"
  exit 1
fi

if [ ! -f "$CONFIG" ]; then
  echo "WebArena config not found: $CONFIG"
  echo "Please create it with your web environment URLs."
  echo "See configs/webarena/env_urls.json for reference."
  exit 1
fi

echo "============================================"
echo " WebArena-Verified Full Benchmark (812 tasks)"
echo " Model:  $MODEL_NAME"
echo " Output: $OUTPUT_DIR"
echo " Config: $CONFIG"
echo "============================================"

"$PYTHON_BIN" "$ROOT_DIR/benchmark_suite/run_webarena_verified.py" \
  --model "$MODEL_NAME" \
  --base-url "$GLM_BASE_URL" \
  --api-key "$GLM_API_KEY" \
  --output-dir "$OUTPUT_DIR" \
  --config "$CONFIG" \
  --run-eval
