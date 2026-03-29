#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WEBARENA_DIR="${1:-$ROOT_DIR/vendor/webarena-verified}"
WEBARENA_REPO_URL="${WEBARENA_REPO_URL:-https://github.com/ServiceNow/webarena-verified.git}"

if [ ! -d "$WEBARENA_DIR/.git" ]; then
  mkdir -p "$(dirname "$WEBARENA_DIR")"
  git clone --depth=1 "$WEBARENA_REPO_URL" "$WEBARENA_DIR"
fi

cd "$ROOT_DIR"
uv sync
uv pip install webarena-verified 2>/dev/null || echo "webarena-verified pip package not available; using vendor repo directly"

echo "WebArena-Verified ready at: $WEBARENA_DIR"
