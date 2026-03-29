#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

OUTPUT_ROOT="${1:-$ROOT_DIR/dumps/opencode_until_first_candidate}"
REPOS_ROOT="${2:-$ROOT_DIR/dumps/repos_until_first_candidate}"
MODEL_NAME="${3:-${GLM_MODEL:-github-copilot/glm-5}}"
LIMIT="${LIMIT:-10}"
MAX_WORKERS="${MAX_WORKERS:-10}"
TIMEOUT_SEC="${TIMEOUT_SEC:-86400}"

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

mkdir -p "$OUTPUT_ROOT" "$REPOS_ROOT"

"$PYTHON_BIN" "$ROOT_DIR/benchmark_suite/run_opencode_swebench.py" \
  --output-root "$OUTPUT_ROOT" \
  --repos-root "$REPOS_ROOT" \
  --model "$MODEL_NAME" \
  --base-url "$GLM_BASE_URL" \
  --api-key "$GLM_API_KEY" \
  --split test \
  --limit "$LIMIT" \
  --max-workers "$MAX_WORKERS" \
  --timeout-sec "$TIMEOUT_SEC" \
  >"$OUTPUT_ROOT/runner.stdout.log" 2>"$OUTPUT_ROOT/runner.stderr.log" &

RUNNER_PID=$!
echo "$RUNNER_PID" > "$OUTPUT_ROOT/runner.pid"

cleanup() {
  kill "$RUNNER_PID" 2>/dev/null || true
}
trap cleanup EXIT

while kill -0 "$RUNNER_PID" 2>/dev/null; do
  CANDIDATE="$("$PYTHON_BIN" - "$OUTPUT_ROOT" <<'PY'
import json, sys
from pathlib import Path

root = Path(sys.argv[1])
for summary_path in sorted(root.glob("*/summary.json")):
    data = json.loads(summary_path.read_text())
    status = data.get("status", "")
    if (
        data.get("exit_code") == 0
        and not data.get("timed_out")
        and data.get("diff_len", 0) > 0
        and not data.get("reverted_test_files")
        and " M test/" not in status
        and " M tests/" not in status
    ):
        print(summary_path.parent)
        raise SystemExit(0)
raise SystemExit(1)
PY
  )" || true

  if [[ -n "${CANDIDATE:-}" ]]; then
    echo "FIRST_CANDIDATE=$CANDIDATE"
    kill "$RUNNER_PID" 2>/dev/null || true
    wait "$RUNNER_PID" 2>/dev/null || true
    trap - EXIT
    exit 0
  fi

  sleep 15
done

wait "$RUNNER_PID"
trap - EXIT
