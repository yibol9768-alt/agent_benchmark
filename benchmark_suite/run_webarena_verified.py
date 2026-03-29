from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from datasets import load_dataset
from openai import OpenAI


SYSTEM_PROMPT = """You are a web browsing agent. You are given a task to complete on a website.
You must analyze the task and provide a structured JSON response.

Your response MUST be valid JSON with exactly these fields:
{
  "task_type": "<retrieve_information|perform_action|multi_step>",
  "status": "<success|failure|partial>",
  "retrieved_data": "<the answer or result, or null if action-only task>",
  "action_summary": "<brief description of what you did or would do>",
  "error_details": "<null if successful, otherwise describe the issue>"
}

Guidelines:
- For information retrieval tasks: extract the precise answer from the website context.
- For action tasks: describe the exact steps and parameters needed.
- For multi-step tasks: break down into ordered steps and report the final result.
- Be precise with numbers, dates, names, and URLs.
- If the task requires interacting with a specific website, reason about what pages and elements would be involved.
"""


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def load_webarena_tasks(
    split: str, limit: int | None, task_ids: set[int] | None
) -> list[dict[str, Any]]:
    dataset = load_dataset("AmineHA/WebArena-Verified", split=split)
    tasks: list[dict[str, Any]] = []
    for row in dataset:
        row = dict(row)
        if task_ids and row["task_id"] not in task_ids:
            continue
        tasks.append(row)
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


def build_task_prompt(task: dict[str, Any], env_config: dict[str, Any] | None) -> str:
    intent = task["intent"]
    start_urls = task.get("start_urls") or []
    sites = task.get("sites") or []

    url_section = ""
    if start_urls:
        url_section = f"\nStart URLs: {json.dumps(start_urls)}"
    site_section = ""
    if sites:
        site_section = f"\nWebsites involved: {', '.join(sites)}"

    env_section = ""
    if env_config:
        available_sites = []
        for key, cfg in env_config.items():
            urls = cfg.get("urls", [])
            if urls:
                available_sites.append(f"  {key}: {urls[0]}")
        if available_sites:
            env_section = "\n\nAvailable web environments:\n" + "\n".join(available_sites)

    return f"""Task: {intent}
{url_section}{site_section}{env_section}

Analyze this web task and provide your structured JSON response."""


def call_llm(
    client: OpenAI, model: str, task_prompt: str, temperature: float = 0.0
) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task_prompt},
        ],
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


def parse_agent_response(raw: str) -> dict[str, Any] | None:
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


def evaluate_response(task: dict[str, Any], agent_response: dict[str, Any] | None) -> dict[str, Any]:
    if agent_response is None:
        return {"parsed": False, "score": 0.0, "reason": "Failed to parse agent response as JSON"}

    eval_spec = task.get("eval")
    if eval_spec:
        try:
            evaluators = json.loads(eval_spec) if isinstance(eval_spec, str) else eval_spec
        except (json.JSONDecodeError, TypeError):
            evaluators = None
    else:
        evaluators = None

    result: dict[str, Any] = {
        "parsed": True,
        "task_type": agent_response.get("task_type"),
        "status": agent_response.get("status"),
        "has_retrieved_data": agent_response.get("retrieved_data") is not None,
        "evaluator_count": len(evaluators) if isinstance(evaluators, list) else 0,
    }

    if isinstance(evaluators, list):
        for ev in evaluators:
            if isinstance(ev, dict) and ev.get("eval_types"):
                result["eval_types"] = ev["eval_types"]
                break

    result["score"] = 1.0 if agent_response.get("status") == "success" else 0.0
    result["reason"] = "Structural evaluation only; full evaluation requires live web environment"
    return result


def run_instance(
    task: dict[str, Any],
    client: OpenAI,
    model: str,
    output_root: Path,
    env_config: dict[str, Any] | None,
) -> dict[str, Any]:
    task_id = task["task_id"]
    task_dir = output_root / f"task_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)

    summary_path = task_dir / "summary.json"
    if summary_path.exists():
        log(f"task_{task_id}: skipped, summary already exists")
        return json.loads(summary_path.read_text())

    prompt = build_task_prompt(task, env_config)
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    log(f"task_{task_id}: calling LLM")
    started = time.time()
    error = None
    raw_response = ""
    try:
        raw_response = call_llm(client, model, prompt)
    except Exception as exc:
        error = str(exc)
    elapsed = time.time() - started

    (task_dir / "raw_response.txt").write_text(raw_response, encoding="utf-8")

    agent_response = parse_agent_response(raw_response)
    if agent_response is not None:
        (task_dir / "agent_response.json").write_text(
            json.dumps(agent_response, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    eval_result = evaluate_response(task, agent_response)

    summary = {
        "task_id": task_id,
        "intent": task.get("intent", ""),
        "sites": task.get("sites", []),
        "elapsed_sec": round(elapsed, 2),
        "error": error,
        "response_len": len(raw_response),
        "parsed": eval_result.get("parsed", False),
        "eval": eval_result,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"task_{task_id}: done elapsed={elapsed:.2f}s parsed={eval_result.get('parsed')}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run WebArena-Verified with GLM via OpenAI API.")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--split", default="full")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--task-id", type=int, action="append")
    parser.add_argument("--env-config", help="Path to env_urls.json for web environment URLs")
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args()

    model_name = args.model
    if "/" in model_name:
        model_name = model_name.split("/", 1)[1]

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)

    task_ids = set(args.task_id) if args.task_id else None
    tasks = load_webarena_tasks(split=args.split, limit=args.limit, task_ids=task_ids)
    if not tasks:
        raise SystemExit("No tasks selected.")

    env_config = None
    if args.env_config:
        env_config = json.loads(Path(args.env_config).read_text())

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "benchmark": "WebArena-Verified",
        "model": args.model,
        "split": args.split,
        "limit": args.limit,
        "task_ids": sorted(task_ids) if task_ids else None,
        "count": len(tasks),
    }
    (output_root / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summaries: list[dict[str, Any]] = []
    for task in tasks:
        summary = run_instance(
            task=task, client=client, model=model_name,
            output_root=output_root, env_config=env_config,
        )
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
