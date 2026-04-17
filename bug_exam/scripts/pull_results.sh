#!/bin/bash
# scripts/pull_results.sh — fetch results from westd, rebuild leaderboard
#
# Usage:
#   bash scripts/pull_results.sh                # pull summary + leaderboard
#   bash scripts/pull_results.sh --full         # also pull per-instance exam.json + diffs
#   bash scripts/pull_results.sh --screen       # pull screening results only
set -euo pipefail

REMOTE="westd"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOCAL_DUMPS="$PROJECT_DIR/dumps/swebench_pro_scale"
REMOTE_DUMPS="/root/bug_exam/dumps/swebench_pro_scale"

mkdir -p "$LOCAL_DUMPS"

case "${1:-}" in
    --screen)
        echo "=== pulling screening results"
        ssh "$REMOTE" "wsl -d Ubuntu -- cat /root/bug_exam/configs/screened_instances.json" \
            > "$PROJECT_DIR/configs/screened_instances.json" 2>/dev/null \
            && echo "saved to configs/screened_instances.json" \
            || echo "no screening results yet"

        # Show summary
        if [ -f "$PROJECT_DIR/configs/screened_instances.json" ]; then
            python3 -c "
import json
d = json.load(open('$PROJECT_DIR/configs/screened_instances.json'))
print(f\"total: {d['total']}, viable: {d['viable']}\")
for r in d['instances'][:10]:
    v = 'OK' if r.get('viable') else 'SKIP'
    print(f\"  [{v}] {r['instance_id'][:50]}... pass={r.get('baseline_pass_count',0)}\")
if d['total'] > 10: print(f'  ... and {d[\"total\"]-10} more')
"
        fi
        exit 0
        ;;
esac

echo "=== pulling summary + tickets"
ssh "$REMOTE" "wsl -d Ubuntu -- cat ${REMOTE_DUMPS}/summary.jsonl" \
    > "$LOCAL_DUMPS/summary.jsonl" 2>/dev/null \
    && echo "  summary.jsonl pulled" \
    || echo "  no summary.jsonl yet"

ssh "$REMOTE" "wsl -d Ubuntu -- cat ${REMOTE_DUMPS}/tickets.jsonl" \
    > "$LOCAL_DUMPS/tickets.jsonl" 2>/dev/null \
    && echo "  tickets.jsonl pulled" \
    || echo "  no tickets.jsonl yet"

# Pull screened instances config
ssh "$REMOTE" "wsl -d Ubuntu -- cat /root/bug_exam/configs/screened_instances.json" \
    > "$PROJECT_DIR/configs/screened_instances.json" 2>/dev/null || true

if [ "${1:-}" = "--full" ]; then
    echo "=== pulling per-instance results (exam.json + diffs)"
    # Use tar over ssh since rsync may not be available on Windows SSH
    ssh "$REMOTE" "wsl -d Ubuntu -- bash -lc '
        cd ${REMOTE_DUMPS} 2>/dev/null && \
        find . -name exam.json -o -name \"*.diff\" -o -name run.json -o -name problem_statement.md | \
        tar czf - -T - 2>/dev/null
    '" | tar xzf - -C "$LOCAL_DUMPS" 2>/dev/null \
        && echo "  per-instance results pulled" \
        || echo "  no per-instance results yet"
fi

# Rebuild leaderboard locally
if [ -f "$LOCAL_DUMPS/summary.jsonl" ] && [ -s "$LOCAL_DUMPS/summary.jsonl" ]; then
    echo "=== rebuilding leaderboard"
    cd "$PROJECT_DIR"
    PYTHONPATH=. python3 scripts/render_leaderboard.py \
        --summary "$LOCAL_DUMPS/summary.jsonl" \
        --out-dir "$LOCAL_DUMPS/leaderboard" 2>/dev/null \
        && echo "" && cat "$LOCAL_DUMPS/leaderboard/table.md" \
        || echo "  leaderboard rebuild failed (may need venv)"
else
    echo "=== no summary data yet, skipping leaderboard"
fi

echo ""
echo "done. results at: $LOCAL_DUMPS/"
