#!/bin/bash
# scripts/deploy.sh — push bug_exam code to westd (Ubuntu WSL)
#
# Usage:
#   bash scripts/deploy.sh              # just deploy code
#   bash scripts/deploy.sh --screen     # deploy + start screening
#   bash scripts/deploy.sh --batch      # deploy + start batch run
set -euo pipefail

REMOTE="westd"
REMOTE_DIR="/root/bug_exam"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== packing source from $PROJECT_DIR"
TARBALL="/tmp/bug_exam_deploy.tar.gz"
cd "$PROJECT_DIR/.."
tar czf "$TARBALL" \
    --exclude='bug_exam/.venv' \
    --exclude='bug_exam/dumps' \
    --exclude='bug_exam/data' \
    --exclude='bug_exam/__pycache__' \
    --exclude='bug_exam/*/__pycache__' \
    --exclude='bug_exam/.git' \
    --exclude='bug_exam/.pytest_cache' \
    bug_exam/

echo "=== uploading to $REMOTE"
scp "$TARBALL" "${REMOTE}:C:/tools/bug_exam_deploy.tar.gz"

echo "=== extracting in WSL"
ssh "$REMOTE" "wsl -d Ubuntu -- bash -lc '
    cd /root
    tar xzf /mnt/c/tools/bug_exam_deploy.tar.gz
    cd ${REMOTE_DIR}
    if [ ! -d .venv ]; then
        echo \"creating venv...\"
        python3 -m venv .venv
        .venv/bin/pip install -e . 2>&1 | tail -3
    else
        echo \"venv exists, syncing deps...\"
        .venv/bin/pip install -e . -q 2>&1 | tail -3
    fi
    echo \"deploy done: \$(date)\"
'"

# Optional: kick off screening or batch
case "${1:-}" in
    --screen)
        echo "=== starting screening (nohup)"
        ssh "$REMOTE" "wsl -d Ubuntu -- bash -lc '
            cd ${REMOTE_DIR}
            nohup .venv/bin/python -u scripts/screen_swebench_pro.py \
                --jsonl /root/SWE-bench_Pro-os/helper_code/sweap_eval_full_v2.jsonl \
                --swepro-root /root/SWE-bench_Pro-os \
                --out configs/screened_instances.json \
                --repos ansible/ansible,qutebrowser/qutebrowser,internetarchive/openlibrary \
                --skip-existing --verbose \
                > /tmp/screen.log 2>&1 &
            echo \"screening PID: \$!\"
            echo \"tail -f /tmp/screen.log to monitor\"
        '"
        ;;
    --batch)
        echo "=== starting batch run (nohup)"
        ssh "$REMOTE" "wsl -d Ubuntu -- bash -lc '
            cd ${REMOTE_DIR}
            export GLM_API_KEY=sk-EZaFIa6PKx3VDyWBFWvur3irS8K6lRI7qexqIAFcQyXtD1eD
            export GLM_BASE_URL=http://35.220.164.252:3888/v1/
            export GLM_MODEL=glm-5
            export BUG_EXAM_PROVIDER=glm
            export ANTHROPIC_API_KEY=\$GLM_API_KEY
            export ANTHROPIC_BASE_URL=http://35.220.164.252:3888/
            export ANTHROPIC_MODEL=glm-5
            export ANTHROPIC_AUTH_TOKEN=\$GLM_API_KEY
            nohup .venv/bin/python -u scripts/run_swebench_pro_batch.py \
                --instance-file configs/screened_instances.json \
                --bands 1x1 \
                --solvers claude_direct,openhands \
                --jsonl /root/SWE-bench_Pro-os/helper_code/sweap_eval_full_v2.jsonl \
                --swepro-root /root/SWE-bench_Pro-os \
                --workdir-root /root/bugexam_scale/work \
                --runs-root /root/bugexam_scale/runs \
                --out-root dumps/swebench_pro_scale \
                --n-draws 6 --reuse-checkout --verbose \
                > /tmp/batch.log 2>&1 &
            echo \"batch PID: \$!\"
            echo \"tail -f /tmp/batch.log to monitor\"
        '"
        ;;
    "")
        echo "=== deploy only (no task started)"
        echo "    use --screen or --batch to kick off a task"
        ;;
    *)
        echo "unknown option: $1 (use --screen or --batch)"
        exit 1
        ;;
esac
