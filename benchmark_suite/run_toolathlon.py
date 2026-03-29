from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from openai import OpenAI


TOOLATHLON_SYSTEM_PROMPT = """You are a tool-use agent. You are given a task that requires calling tools (APIs, CLI commands, or web services) to accomplish a goal.

Analyze the task carefully and provide a structured response:
1. Break the task into steps.
2. For each step, specify which tool/API to call and with what parameters.
3. Provide the final answer or result.

Your response MUST be valid JSON:
{
  "task_name": "<name of the task>",
  "steps": [
    {
      "step": 1,
      "tool": "<tool or API name>",
      "parameters": {},
      "expected_result": "<what this step should produce>"
    }
  ],
  "final_answer": "<the final result or confirmation>",
  "status": "<success|failure|partial>"
}
"""


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def load_toolathlon_tasks(limit: int | None, task_names: set[str] | None) -> list[dict[str, Any]]:
    try:
        from datasets import load_dataset
        dataset = load_dataset("hkust-nlp/Toolathlon-Trajectories", split="train")
        tasks: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for row in dataset:
            row = dict(row)
            name = row.get("task_name") or row.get("modelname_run", "").split("_")[0]
            if not name or name in seen_names:
                continue
            if task_names and name not in task_names:
                continue
            seen_names.add(name)
            tasks.append(row)
            if limit is not None and len(tasks) >= limit:
                break
        return tasks
    except Exception as exc:
        log(f"Could not load Toolathlon dataset from HuggingFace: {exc}")
        return []


def build_task_prompt(task: dict[str, Any]) -> str:
    name = task.get("task_name") or task.get("modelname_run", "unknown")
    messages = task.get("messages")
    config = task.get("config")

    context_parts = [f"Task: {name}"]

    if isinstance(messages, list) and messages:
        for msg in messages[:3]:
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if content:
                    context_parts.append(f"\nUser instruction: {content[:2000]}")
                    break

    if isinstance(config, dict):
        servers = config.get("mcpServers") or config.get("mcp_servers") or {}
        if servers:
            tool_names = list(servers.keys())[:10]
            context_parts.append(f"\nAvailable tools/services: {', '.join(tool_names)}")

    context_parts.append("\nAnalyze this task and provide your structured JSON response with the steps needed.")
    return "\n".join(context_parts)


def call_llm(
    client: OpenAI, model: str, task_prompt: str, temperature: float = 0.0
) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": TOOLATHLON_SYSTEM_PROMPT},
            {"role": "user", "content": task_prompt},
        ],
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


def parse_response(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        start = 1
        end = len(lines)
        for i, line in enumerate(lines[1:], 1):
            if line.strip().startswith("```"):
                end = i
                break
        raw = "\n".join(lines[start:end])
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def run_via_official_client(
    toolathlon_dir: Path,
    model: str,
    base_url: str,
    api_key: str,
    task_names: list[str] | None,
    output_dir: Path,
    server_host: str | None,
) -> int:
    eval_client = toolathlon_dir / "eval_client.py"
    if not eval_client.exists():
        log(f"eval_client.py not found at {eval_client}")
        return 1

    cmd = [
        sys.executable, str(eval_client), "run",
        "--mode", "public",
        "--base-url", base_url,
        "--model-name", model,
        "--api-key", api_key,
    ]
    if server_host:
        cmd.extend(["--server-host", server_host])
    if task_names:
        for name in task_names:
            cmd.extend(["--task", name])

    env = os.environ.copy()
    env["TOOLATHLON_OPENAI_BASE_URL"] = base_url
    env["TOOLATHLON_OPENAI_API_KEY"] = api_key

    log(f"Running official eval_client: {' '.join(cmd[:6])}...")
    proc = subprocess.run(cmd, cwd=toolathlon_dir, env=env, text=True, capture_output=True)
    (output_dir / "eval_client_stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (output_dir / "eval_client_stderr.txt").write_text(proc.stderr, encoding="utf-8")
    log(f"eval_client exit_code={proc.returncode}")
    return proc.returncode


def run_instance(
    task: dict[str, Any],
    client: OpenAI,
    model: str,
    output_root: Path,
) -> dict[str, Any]:
    name = task.get("task_name") or task.get("modelname_run", "unknown")
    task_dir = output_root / f"task_{name}"
    task_dir.mkdir(parents=True, exist_ok=True)

    summary_path = task_dir / "summary.json"
    if summary_path.exists():
        log(f"{name}: skipped, summary already exists")
        return json.loads(summary_path.read_text())

    prompt = build_task_prompt(task)
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    log(f"{name}: calling LLM")
    started = time.time()
    error = None
    raw_response = ""
    try:
        raw_response = call_llm(client, model, prompt)
    except Exception as exc:
        error = str(exc)
    elapsed = time.time() - started

    (task_dir / "raw_response.txt").write_text(raw_response, encoding="utf-8")

    parsed = parse_response(raw_response)
    if parsed is not None:
        (task_dir / "agent_response.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    summary = {
        "task_name": name,
        "elapsed_sec": round(elapsed, 2),
        "error": error,
        "response_len": len(raw_response),
        "parsed": parsed is not None,
        "status": parsed.get("status") if parsed else None,
        "steps_count": len(parsed.get("steps", [])) if parsed else 0,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"{name}: done elapsed={elapsed:.2f}s parsed={parsed is not None}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Toolathlon with GLM via OpenAI API.")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--task-name", action="append")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--use-official-client", action="store_true",
        help="Use official eval_client.py from vendor/toolathlon (requires Docker environments)",
    )
    parser.add_argument("--server-host", help="Toolathlon server host for official eval_client")
    args = parser.parse_args()

    model_name = args.model
    if "/" in model_name:
        model_name = model_name.split("/", 1)[1]

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    project_root = Path(__file__).resolve().parent.parent
    toolathlon_dir = project_root / "vendor" / "toolathlon"

    if args.use_official_client:
        if not toolathlon_dir.exists():
            raise SystemExit(
                f"Toolathlon repo not found at {toolathlon_dir}. "
                "Run: bash scripts/setup_toolathlon.sh"
            )
        exit_code = run_via_official_client(
            toolathlon_dir=toolathlon_dir,
            model=model_name,
            base_url=args.base_url,
            api_key=args.api_key,
            task_names=args.task_name,
            output_dir=output_root,
            server_host=args.server_host,
        )
        raise SystemExit(exit_code)

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)

    task_names = set(args.task_name) if args.task_name else None
    tasks = load_toolathlon_tasks(limit=args.limit, task_names=task_names)
    if not tasks:
        log("No tasks loaded from HuggingFace. Falling back to dummy task for API connectivity test.")
        tasks = [{"task_name": "api_test", "messages": [{"role": "user", "content": "List my upcoming calendar events for this week."}]}]

    manifest = {
        "benchmark": "Toolathlon",
        "model": args.model,
        "limit": args.limit,
        "task_names": sorted(task_names) if task_names else None,
        "count": len(tasks),
    }
    (output_root / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summaries: list[dict[str, Any]] = []
    for task in tasks:
        summary = run_instance(task=task, client=client, model=model_name, output_root=output_root)
        summaries.append(summary)

    total = len(summaries)
    parsed = sum(1 for s in summaries if s.get("parsed"))
    errors = sum(1 for s in summaries if s.get("error"))
    log(f"Finished: {total} tasks, {parsed} parsed, {errors} errors")

    results_path = output_root / "results_summary.json"
    results_path.write_text(
        json.dumps(
            {"total": total, "parsed": parsed, "errors": errors, "summaries": summaries},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
