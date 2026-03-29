#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

OUTPUT_ROOT="${1:-$ROOT_DIR/dumps/opencode_swebench_pro_full}"
REPOS_ROOT="${2:-$ROOT_DIR/dumps/repos}"
MODEL_NAME="${3:-${GLM_MODEL:-github-copilot/glm-5}}"
MAX_WORKERS="${MAX_WORKERS:-8}"
TIMEOUT_SEC="${TIMEOUT_SEC:-900}"
LIMIT="${LIMIT:-}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Project venv not found. Run: cd $ROOT_DIR && uv sync"
  exit 1
fi

if [[ "$MODEL_NAME" != */* ]]; then
  MODEL_NAME="github-copilot/$MODEL_NAME"
fi

if [ -z "${GLM_API_KEY:-}" ] || [ -z "${GLM_BASE_URL:-}" ]; then
  echo "GLM_API_KEY and GLM_BASE_URL must be exported"
  exit 1
fi

mkdir -p "$OUTPUT_ROOT" "$REPOS_ROOT"

LIMIT_FLAG=""
if [ -n "$LIMIT" ]; then
  LIMIT_FLAG="--limit $LIMIT"
fi

echo "========================================"
echo " SWE-Bench Pro Full Benchmark"
echo " Model:      $MODEL_NAME"
echo " Workers:    $MAX_WORKERS"
echo " Timeout:    ${TIMEOUT_SEC}s per task"
echo " Output:     $OUTPUT_ROOT"
echo " Resume:     enabled (skip completed)"
echo "========================================"

"$PYTHON_BIN" "$ROOT_DIR/benchmark_suite/run_opencode_swebench.py" \
  --output-root "$OUTPUT_ROOT" \
  --repos-root "$REPOS_ROOT" \
  --model "$MODEL_NAME" \
  --base-url "$GLM_BASE_URL" \
  --api-key "$GLM_API_KEY" \
  --split test \
  --max-workers "$MAX_WORKERS" \
  --timeout-sec "$TIMEOUT_SEC" \
  --resume \
  $LIMIT_FLAG

echo ""
echo "Patch generation done. Collecting results..."

"$PYTHON_BIN" - "$OUTPUT_ROOT" <<'PYTHON'
import json, sys
from pathlib import Path

root = Path(sys.argv[1])
summaries = sorted(root.glob("instance_*/summary.json"))
total = len(summaries)
completed = 0
timed_out = 0
has_patch = 0
empty_patch = 0
errors = 0
total_elapsed = 0.0

for sp in summaries:
    data = json.loads(sp.read_text())
    total_elapsed += data.get("elapsed_sec", 0)
    if data.get("timed_out"):
        timed_out += 1
    if data.get("exit_code") == 0:
        completed += 1
    if data.get("diff_len", 0) > 0:
        has_patch += 1
    else:
        empty_patch += 1
    if data.get("exit_code", 0) != 0 and not data.get("timed_out"):
        errors += 1

print(f"\n{'='*40}")
print(f" Results Summary")
print(f"{'='*40}")
print(f" Total instances:   {total}")
print(f" Completed (ok):    {completed}")
print(f" Timed out:         {timed_out}")
print(f" Errors:            {errors}")
print(f" Has patch:         {has_patch}")
print(f" Empty patch:       {empty_patch}")
print(f" Total time:        {total_elapsed/3600:.1f} hours")
print(f" Avg time/task:     {total_elapsed/max(total,1):.0f}s")
print(f"{'='*40}")

# Write results
results = {
    "total": total, "completed": completed, "timed_out": timed_out,
    "errors": errors, "has_patch": has_patch, "empty_patch": empty_patch,
    "total_elapsed_hours": round(total_elapsed/3600, 2),
    "avg_elapsed_sec": round(total_elapsed/max(total,1), 1),
}
(root / "results_summary.json").write_text(json.dumps(results, indent=2))

# Also create patches.json for official evaluation
patches = []
for sp in summaries:
    data = json.loads(sp.read_text())
    patch_path = sp.parent / "patch.diff"
    if patch_path.exists():
        patch = patch_path.read_text()
        if patch.strip():
            patches.append({
                "instance_id": data["instance_id"],
                "model_name_or_path": "opencode-glm5",
                "model_patch": patch,
            })
(root / "patches_for_eval.json").write_text(json.dumps(patches, indent=2))
print(f" Patches exported:  {len(patches)} (for official eval)")
print(f" File: {root / 'patches_for_eval.json'}")
PYTHON
