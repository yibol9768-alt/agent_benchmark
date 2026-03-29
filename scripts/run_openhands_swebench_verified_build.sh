#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OPENHANDS_DIR="${OPENHANDS_DIR:-$ROOT_DIR/vendor/openhands-benchmarks}"
INSTANCE_ID="${1:-django__django-11333}"
DATASET="${DATASET:-princeton-nlp/SWE-bench_Verified}"
SPLIT="${SPLIT:-test}"
TARGET="${TARGET:-source-minimal}"
IMAGE="${IMAGE:-ghcr.io/openhands/eval-agent-server}"
MAX_WORKERS="${MAX_WORKERS:-4}"

if [ ! -d "$OPENHANDS_DIR" ]; then
  echo "OpenHands benchmarks repo not found at $OPENHANDS_DIR"
  echo "Run: bash $ROOT_DIR/scripts/setup_openhands.sh"
  exit 1
fi

SELECT_FILE="$(mktemp)"
trap 'rm -f "$SELECT_FILE"' EXIT
printf '%s\n' "$INSTANCE_ID" > "$SELECT_FILE"

(
  cd "$OPENHANDS_DIR"
  uv run python -m benchmarks.swebench.build_images \
    --dataset "$DATASET" \
    --split "$SPLIT" \
    --image "$IMAGE" \
    --target "$TARGET" \
    --select "$SELECT_FILE" \
    --max-workers "$MAX_WORKERS"
)
