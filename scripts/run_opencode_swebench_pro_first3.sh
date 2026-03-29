#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

OUTPUT_ROOT="${1:-$ROOT_DIR/dumps/opencode_swebench_pro_first3}"
REPOS_ROOT="${2:-$ROOT_DIR/dumps/repos_first3}"
MODEL_NAME="${3:-${GLM_MODEL:-github-copilot/glm-5}}"
TIMEOUT_SEC="${TIMEOUT_SEC:-180}"
MAX_WORKERS="${MAX_WORKERS:-3}"
RESUME_FLAG="${RESUME_FLAG:---resume}"

if [[ "$MODEL_NAME" != */* ]]; then
  MODEL_NAME="github-copilot/$MODEL_NAME"
fi

if [ -z "${GLM_API_KEY:-}" ] || [ -z "${GLM_BASE_URL:-}" ]; then
  echo "GLM_API_KEY and GLM_BASE_URL must be exported"
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Project venv not found. Run: cd $ROOT_DIR && uv sync"
  exit 1
fi

"$PYTHON_BIN" "$ROOT_DIR/benchmark_suite/run_opencode_swebench.py" \
  --output-root "$OUTPUT_ROOT" \
  --repos-root "$REPOS_ROOT" \
  --model "$MODEL_NAME" \
  --base-url "$GLM_BASE_URL" \
  --api-key "$GLM_API_KEY" \
  --split test \
  --limit 3 \
  --max-workers "$MAX_WORKERS" \
  $RESUME_FLAG \
  --timeout-sec "$TIMEOUT_SEC"
