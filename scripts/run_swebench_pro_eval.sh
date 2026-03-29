#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 4 ]; then
  echo "Usage: $0 <swe_bench_pro_root> <patch_json> <output_dir> <raw_sample_path> [num_workers] [dockerhub_username] [extra_args...]"
  exit 1
fi

SWE_BENCH_PRO_ROOT="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1")"
PATCH_JSON="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$2")"
OUTPUT_DIR="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$3")"
RAW_SAMPLE_PATH="$4"
NUM_WORKERS="${5:-10}"
DOCKERHUB_USERNAME="${6:-jefzda}"
VENV_DIR="$SWE_BENCH_PRO_ROOT/SWE-agent/.venv"
shift 6 2>/dev/null || true
EXTRA_ARGS=("${@:-}")

mkdir -p "$OUTPUT_DIR"

if [ -x "$VENV_DIR/bin/python" ]; then
  source "$VENV_DIR/bin/activate"
fi

python "$SWE_BENCH_PRO_ROOT/swe_bench_pro_eval.py" \
  --raw_sample_path="$RAW_SAMPLE_PATH" \
  --patch_path="$PATCH_JSON" \
  --output_dir="$OUTPUT_DIR" \
  --scripts_dir="$SWE_BENCH_PRO_ROOT/run_scripts" \
  --num_workers="$NUM_WORKERS" \
  --dockerhub_username="$DOCKERHUB_USERNAME" \
  ${EXTRA_ARGS[@]:+"${EXTRA_ARGS[@]}"}
