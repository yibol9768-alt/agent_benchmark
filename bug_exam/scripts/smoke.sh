#!/usr/bin/env bash
# Phase 1 vertical-slice smoke test.
# Exit criterion: produces a BT leaderboard over 5 Python repos x 2 bands x 3
# solvers (~30 runs) end-to-end, no manual intervention, in under 2 hours.
set -euo pipefail

cd "$(dirname "$0")/.."

: "${GITHUB_TOKEN:?GITHUB_TOKEN env var is required for the harvester}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY env var is required for injector + solvers}"

echo "==> harvest"
bug-exam harvest --language python --max 10

echo "==> envbuild"
bug-exam envbuild --limit 10

echo "==> inject"
bug-exam inject --bands trivial,easy --n-draws 4

echo "==> freeze v0_smoke"
bug-exam freeze --name v0_smoke

echo "==> solve"
bug-exam solve --solvers claude_direct,mini_swe_agent,aider

echo "==> grade"
bug-exam grade

echo "==> score"
bug-exam score

echo "==> report"
bug-exam report
