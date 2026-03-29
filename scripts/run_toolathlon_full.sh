#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
OUTPUT_DIR="${1:-$ROOT_DIR/dumps/toolathlon_full}"
MODEL_NAME="${2:-${GLM_MODEL:-glm-5}}"
WORKERS="${WORKERS:-10}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Project venv not found. Run: cd $ROOT_DIR && uv sync"
  exit 1
fi

if [ -z "${GLM_API_KEY:-}" ] || [ -z "${GLM_BASE_URL:-}" ]; then
  echo "GLM_API_KEY and GLM_BASE_URL must be exported"
  exit 1
fi

if [ ! -f "$ROOT_DIR/vendor/toolathlon/eval_client.py" ]; then
  echo "Toolathlon not found. Run: bash $ROOT_DIR/scripts/setup_toolathlon.sh"
  exit 1
fi

echo "========================================"
echo " Toolathlon Full Benchmark (108 tasks)"
echo " Model:   $MODEL_NAME"
echo " Workers: $WORKERS"
echo " Output:  $OUTPUT_DIR"
echo " Monitor: tail -f $OUTPUT_DIR/client.log"
echo "========================================"

"$PYTHON_BIN" "$ROOT_DIR/benchmark_suite/run_toolathlon.py" \
  --model "$MODEL_NAME" \
  --base-url "$GLM_BASE_URL" \
  --api-key "$GLM_API_KEY" \
  --output-dir "$OUTPUT_DIR" \
  --workers "$WORKERS"
