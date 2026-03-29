#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/zsk8888/Desktop/lyb/agent_benchmark"
OPENHANDS_DIR="${1:-$ROOT_DIR/vendor/openhands-benchmarks}"
OPENHANDS_REPO_URL="${OPENHANDS_REPO_URL:-https://github.com/OpenHands/benchmarks.git}"
UV_BIN="${HOME}/.local/bin/uv"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found, installing via official installer"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if ! command -v uv >/dev/null 2>&1 && [ -x "$UV_BIN" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv installation failed"
  exit 1
fi

if [ ! -d "$OPENHANDS_DIR/.git" ]; then
  mkdir -p "$(dirname "$OPENHANDS_DIR")"
  git clone --depth=1 "$OPENHANDS_REPO_URL" "$OPENHANDS_DIR"
fi

git -C "$OPENHANDS_DIR" submodule update --init --recursive

(
  cd "$OPENHANDS_DIR"
  uv sync --dev
)

echo "OpenHands benchmarks ready at: $OPENHANDS_DIR"
