#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/zsk8888/Desktop/lyb/agent_benchmark"
OPENHANDS_DIR="${OPENHANDS_DIR:-$ROOT_DIR/vendor/openhands-benchmarks}"
INPUT_FILE="${1:-$ROOT_DIR/dumps/openhands_verified_smoke/output.jsonl}"
RUN_ID="${2:-openhands_verified_eval}"
DATASET="${DATASET:-princeton-nlp/SWE-bench_Verified}"
SPLIT="${SPLIT:-test}"
WORKERS="${WORKERS:-1}"
TIMEOUT="${TIMEOUT:-1800}"
MODAL_FLAG="${MODAL_FLAG:---no-modal}"

if [ ! -d "$OPENHANDS_DIR" ]; then
  echo "OpenHands benchmarks repo not found at $OPENHANDS_DIR"
  echo "Run: bash $ROOT_DIR/scripts/setup_openhands.sh"
  exit 1
fi

if [ -d "$INPUT_FILE" ]; then
  LATEST_OUTPUT="$(find "$INPUT_FILE" -type f -name output.jsonl | sort | tail -n 1)"
  if [ -z "$LATEST_OUTPUT" ]; then
    echo "No output.jsonl found under directory: $INPUT_FILE"
    exit 1
  fi
  INPUT_FILE="$LATEST_OUTPUT"
fi

if [ ! -f "$INPUT_FILE" ]; then
  echo "Input file not found: $INPUT_FILE"
  exit 1
fi

(
  cd "$OPENHANDS_DIR"
  uv run swebench-eval "$INPUT_FILE" \
    --dataset "$DATASET" \
    --split "$SPLIT" \
    --workers "$WORKERS" \
    --run-id "$RUN_ID" \
    --timeout "$TIMEOUT" \
    "$MODAL_FLAG"
)
