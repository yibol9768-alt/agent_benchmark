#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLATHLON_DIR="${1:-$ROOT_DIR/vendor/toolathlon}"
TOOLATHLON_REPO_URL="${TOOLATHLON_REPO_URL:-https://github.com/hkust-nlp/Toolathlon.git}"

if [ ! -d "$TOOLATHLON_DIR/.git" ]; then
  mkdir -p "$(dirname "$TOOLATHLON_DIR")"
  git clone --depth=1 "$TOOLATHLON_REPO_URL" "$TOOLATHLON_DIR"
fi

cd "$ROOT_DIR"
uv sync

echo "Toolathlon ready at: $TOOLATHLON_DIR"
echo "NOTE: Toolathlon tasks require Docker environments. See vendor/toolathlon/README.md for setup."
