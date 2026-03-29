#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <swe_bench_pro_root>"
  exit 1
fi

SWE_BENCH_PRO_ROOT="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1")"
SWE_AGENT_DIR="$SWE_BENCH_PRO_ROOT/SWE-agent"
VENV_DIR="$SWE_AGENT_DIR/.venv"

if [ ! -d "$SWE_BENCH_PRO_ROOT" ]; then
  echo "SWE-Bench Pro root not found: $SWE_BENCH_PRO_ROOT"
  exit 1
fi

echo "Initializing official submodules"
git -C "$SWE_BENCH_PRO_ROOT" submodule update --init --recursive SWE-agent mini-swe-agent

if [ ! -f "$SWE_AGENT_DIR/pyproject.toml" ]; then
  echo "SWE-agent submodule is still incomplete: $SWE_AGENT_DIR"
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel

echo "Installing SWE-agent locally into virtualenv"
python -m pip install -e "$SWE_AGENT_DIR"

echo "Applying official SWE-Rex patches"
python "$SWE_AGENT_DIR/swerex_patches/patch.py" --yes

echo "Setup complete"
