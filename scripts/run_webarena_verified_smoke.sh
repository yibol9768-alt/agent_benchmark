#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
OUTPUT_DIR="${1:-$ROOT_DIR/dumps/webarena_verified_smoke}"
MODEL_NAME="${2:-${GLM_MODEL:-glm-5}}"
LIMIT="${LIMIT:-3}"
ENV_CONFIG="${ENV_CONFIG:-$ROOT_DIR/configs/webarena/env_urls.json}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Project venv not found. Run: cd $ROOT_DIR && uv sync"
  exit 1
fi

if [ -z "${GLM_API_KEY:-}" ] || [ -z "${GLM_BASE_URL:-}" ]; then
  echo "GLM_API_KEY and GLM_BASE_URL must be exported"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

ENV_FLAG=""
if [ -f "$ENV_CONFIG" ]; then
  ENV_FLAG="--env-config $ENV_CONFIG"
fi

"$PYTHON_BIN" "$ROOT_DIR/benchmark_suite/run_webarena_verified.py" \
  --output-root "$OUTPUT_DIR" \
  --model "$MODEL_NAME" \
  --base-url "$GLM_BASE_URL" \
  --api-key "$GLM_API_KEY" \
  --limit "$LIMIT" \
  $ENV_FLAG
