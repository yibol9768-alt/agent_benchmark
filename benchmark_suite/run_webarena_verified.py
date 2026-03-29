"""Run WebArena-Verified evaluation using the official webarena-verified framework.

This script implements a web browsing agent powered by glm-5 that:
1. Receives task definitions from the WebArena-Verified dataset
2. Uses glm-5 to analyze web tasks and reason about actions
3. Produces structured agent responses (agent_response.json)
4. Calls webarena-verified eval to score results deterministically

Prerequisites:
    - Web environments must be running (Docker containers for GitLab, shopping, etc.)
    - See scripts/setup_webarena.sh for environment setup
    - Config file with environment URLs (configs/webarena/env_urls.json)

Usage:
    .venv/bin/python benchmark_suite/run_webarena_verified.py \
        --model glm-5 \
        --base-url "$GLM_BASE_URL" \
        --api-key "$GLM_API_KEY" \
        --output-dir dumps/webarena_glm5 \
        --config configs/webarena/webarena_config.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from datasets import load_dataset
from openai import OpenAI


AGENT_SYSTEM_PROMPT = """You are a web browsing agent tasked with completing tasks on real websites.

You will receive a task description and information about available web environments.
Analyze the task carefully and determine:
1. What type of task this is (retrieve information, perform an action, or navigate)
2. What steps you would take to complete it
3. The final answer or result

You MUST respond with a JSON object in this exact format:
{
  "task_type": "RETRIEVE" or "MUTATE" or "NAVIGATE",
  "status": "SUCCESS" or "FAILURE",
  "retrieved_data": ["value1", "value2"] or null,
  "action_summary": "Brief description of actions taken",
  "error_details": null or "description of why it failed"
}

Rules:
- For RETRIEVE tasks: extract precise data (numbers, names, dates, URLs)
- For MUTATE tasks: describe the exact changes made
- For NAVIGATE tasks: confirm navigation was completed
- retrieved_data must be a list of strings, even for single values
- Be precise with numbers, dates, and proper nouns
- If the task requires actual web interaction you cannot perform, set status to FAILURE with explanation
"""


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_tasks(split: str, limit: int | None, task_ids: set[int] | None) -> list[dict[str, Any]]:
    ds = load_dataset("AmineHA/WebArena-Verified", split=split)
    tasks: list[dict[str, Any]] = []
    for row in ds:
        row = dict(row)
        if task_ids and row["task_id"] not in task_ids:
            continue
        tasks.append(row)
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


def build_prompt(task: dict[str, Any], env_config: dict[str, Any] | None) -> str:
    intent = task["intent"]
    start_urls = task.get("start_urls", [])
    sites = task.get("sites", [])

    parts = [f"Task: {intent}"]

    if start_urls:
        if isinstance(start_urls, str):
            start_urls = json.loads(start_urls) if start_urls.startswith("[") else [start_urls]
        parts.append(f"Start URL(s): {', '.join(str(u) for u in start_urls)}")

    if sites:
        if isinstance(sites, str):
            sites = json.loads(sites) if sites.startswith("[") else [sites]
        parts.append(f"Website(s): {', '.join(str(s) for s in sites)}")

    if env_config:
        env_lines = []
        for key, cfg in env_config.items():
            if isinstance(cfg, dict):
                urls = cfg.get("urls", [])
                creds = cfg.get("credentials", {})
                if urls:
                    cred_str = ""
                    if creds:
                        cred_str = f" (login: {creds.get('username', 'N/A')})"
                    env_lines.append(f"  {key}: {urls[0]}{cred_str}")
        if env_lines:
            parts.append("Available environments:\n" + "\n".join(env_lines))

    template = task.get("intent_template", "")
    if template and template != intent:
        parts.append(f"Template: {template}")

    inst_dict = task.get("instantiation_dict", "")
    if inst_dict:
        if isinstance(inst_dict, str) and inst_dict.strip():
            parts.append(f"Parameters: {inst_dict}")

    parts.append("\nProvide your response as the specified JSON format.")
    return "\n\n".join(parts)


def call_agent(client: OpenAI, model: str, prompt: str) -> tuple[str, dict[str, Any] | None]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
    )
    raw = response.choices[0].message.content or ""

    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines)
        for i, line in enumerate(lines[1:], 1):
            if line.strip().startswith("```"):
                end = i
                break
        text = "\n".join(lines[start:end])

    try:
        parsed = json.loads(text)
        return raw, parsed
    except json.JSONDecodeError:
        return raw, None


def run_task(
    task: dict[str, Any],
    client: OpenAI,
    model: str,
    output_dir: Path,
    env_config: dict[str, Any] | None,
) -> dict[str, Any]:
    task_id = task["task_id"]
    task_dir = output_dir / str(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)

    result_file = task_dir / "result.json"
    if result_file.exists():
        return json.loads(result_file.read_text())

    prompt = build_prompt(task, env_config)
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    started = time.time()
    error = None
    raw = ""
    parsed = None
    try:
        raw, parsed = call_agent(client, model, prompt)
    except Exception as exc:
        error = str(exc)
    elapsed = time.time() - started

    (task_dir / "raw_response.txt").write_text(raw, encoding="utf-8")

    # Write agent_response.json in WebArena-Verified format
    if parsed:
        agent_response = {
            "task_type": parsed.get("task_type", "RETRIEVE"),
            "status": parsed.get("status", "FAILURE"),
            "retrieved_data": parsed.get("retrieved_data"),
            "error_details": parsed.get("error_details"),
        }
    else:
        agent_response = {
            "task_type": "RETRIEVE",
            "status": "FAILURE",
            "retrieved_data": None,
            "error_details": error or "Failed to parse model response",
        }
    (task_dir / "agent_response.json").write_text(
        json.dumps(agent_response, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = {
        "task_id": task_id,
        "intent": task.get("intent", ""),
        "elapsed_sec": round(elapsed, 2),
        "error": error,
        "parsed": parsed is not None,
        "agent_response": agent_response,
    }
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_official_eval(output_dir: Path, config_path: Path, task_ids: list[int]) -> None:
    """Run the official webarena-verified eval on agent outputs."""
    cmd = [
        sys.executable, "-m", "webarena_verified",
        "eval-tasks",
        "--config", str(config_path),
        "--output-dir", str(output_dir),
    ]
    if task_ids:
        cmd.extend(["--task-ids", ",".join(str(t) for t in task_ids)])

    log("Running official webarena-verified evaluation...")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        log("Official evaluation completed")
        if proc.stdout:
            print(proc.stdout)
    else:
        log(f"Official evaluation failed (might need web environments running)")
        if proc.stderr:
            print(proc.stderr[:500])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run WebArena-Verified with glm-5.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--config", help="WebArena config JSON (environment URLs)")
    parser.add_argument("--split", default="full")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--task-id", type=int, action="append")
    parser.add_argument("--run-eval", action="store_true",
                        help="Run official webarena-verified eval after agent (requires web environments)")
    args = parser.parse_args()

    model_name = args.model
    if "/" in model_name:
        model_name = model_name.split("/", 1)[1]

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    env_config = None
    if args.config:
        config_path = Path(args.config).resolve()
        env_config = json.loads(config_path.read_text())

    task_ids = set(args.task_id) if args.task_id else None
    tasks = load_tasks(split=args.split, limit=args.limit, task_ids=task_ids)
    if not tasks:
        print("No tasks selected.")
        sys.exit(1)

    log(f"Running {len(tasks)} WebArena-Verified tasks with {args.model}")

    results = []
    for task in tasks:
        tid = task["task_id"]
        result = run_task(task, client, model_name, output_dir, env_config)
        results.append(result)
        status = result["agent_response"]["status"]
        log(f"  task {tid}: {status} ({result['elapsed_sec']:.1f}s)")

    total = len(results)
    parsed = sum(1 for r in results if r.get("parsed"))
    success = sum(1 for r in results if r["agent_response"]["status"] == "SUCCESS")

    log(f"\nDone: {total} tasks, {parsed} parsed, {success} reported success")

    summary = {"total": total, "parsed": parsed, "success": success, "results": results}
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.run_eval and args.config:
        evaluated_ids = [r["task_id"] for r in results]
        run_official_eval(output_dir, Path(args.config).resolve(), evaluated_ids)


if __name__ == "__main__":
    main()
