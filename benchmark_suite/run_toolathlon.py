"""Run Toolathlon evaluation using the official eval_client.py.

This script wraps the official Toolathlon evaluation client to test
glm-5 (or any OpenAI-compatible model) on the Toolathlon benchmark.

The official eval_client connects to a public evaluation server that
hosts all 108 tasks across 32 real applications. The server handles
Docker containers, MCP tools, and task verification automatically.

Usage:
    .venv/bin/python benchmark_suite/run_toolathlon.py \
        --model glm-5 \
        --base-url "$GLM_BASE_URL" \
        --api-key "$GLM_API_KEY" \
        --output-dir dumps/toolathlon_glm5
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVAL_CLIENT = PROJECT_ROOT / "vendor" / "toolathlon" / "eval_client.py"
DEFAULT_SERVER_HOST = "47.253.6.47"
DEFAULT_SERVER_PORT = 8080


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Toolathlon evaluation via official eval_client."
    )
    parser.add_argument("--model", required=True, help="Model name (e.g. glm-5)")
    parser.add_argument("--base-url", required=True, help="OpenAI-compatible API base URL")
    parser.add_argument("--api-key", required=True, help="API key")
    parser.add_argument("--output-dir", required=True, help="Output directory for results")
    parser.add_argument("--server-host", default=DEFAULT_SERVER_HOST, help="Toolathlon server host")
    parser.add_argument("--server-port", type=int, default=DEFAULT_SERVER_PORT)
    parser.add_argument("--workers", type=int, default=10, help="Parallel workers")
    parser.add_argument("--task-list-file", help="Path to task list file (one task per line)")
    parser.add_argument("--skip-container-restart", action="store_true",
                        help="Skip container restart (for debugging only)")
    parser.add_argument("--job-id", help="Custom job ID (for resuming)")
    args = parser.parse_args()

    if not EVAL_CLIENT.exists():
        print(f"eval_client.py not found at {EVAL_CLIENT}")
        print("Run: bash scripts/setup_toolathlon.sh")
        sys.exit(1)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(EVAL_CLIENT), "run",
        "--mode", "public",
        "--base-url", args.base_url.rstrip("/"),
        "--model-name", args.model,
        "--api-key", args.api_key,
        "--server-host", args.server_host,
        "--server-port", str(args.server_port),
        "--workers", str(args.workers),
        "--output-dir", str(output_dir),
    ]

    if args.task_list_file:
        cmd.extend(["--task-list-file", str(Path(args.task_list_file).resolve())])

    if args.skip_container_restart:
        cmd.append("--skip-container-restart")

    if args.job_id:
        cmd.extend(["--job-id", args.job_id])

    log(f"Starting Toolathlon evaluation")
    log(f"  Model: {args.model}")
    log(f"  Server: {args.server_host}:{args.server_port}")
    log(f"  Workers: {args.workers}")
    log(f"  Output: {output_dir}")
    if args.task_list_file:
        log(f"  Task list: {args.task_list_file}")
    else:
        log(f"  Tasks: all 108 tasks")
    log(f"  Monitor: tail -f {output_dir}/client.log")
    log("")

    proc = subprocess.run(cmd, cwd=PROJECT_ROOT)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
