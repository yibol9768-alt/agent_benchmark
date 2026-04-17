"""Subprocess wrapper launched by OpenHandsSolver.

Runs INSIDE an env that has ``openhands-ai`` installed (python 3.12+).
Reads a task-spec JSON, drives the OpenHands SDK to edit files in the
given workspace, writes a small result JSON with token usage / errors.

This file is never imported by the parent bug_exam process — it's only
invoked as ``python _openhands_runner.py <task.json>``.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

# CRITICAL: this file lives next to ``openhands.py`` (the bug_exam adapter).
# Python would otherwise resolve ``import openhands`` to that sibling. Strip
# our own directory from sys.path so the real openhands-ai package wins.
_SELF_DIR = str(Path(__file__).resolve().parent)
sys.path = [p for p in sys.path if os.path.realpath(p) != _SELF_DIR and p not in ("", ".")]


def _build_task_prompt(problem_statement: str, failing_tests: list[str], workdir: str) -> str:
    failing = "\n  - " + "\n  - ".join(failing_tests) if failing_tests else " (none listed)"
    return (
        f"You are fixing a bug in a Python repository checked out at {workdir}.\n"
        f"The working directory is already your current workspace.\n\n"
        f"Problem statement:\n{problem_statement}\n\n"
        f"Failing tests that must pass after your fix:{failing}\n\n"
        "Instructions:\n"
        "  1. Explore the repo with the terminal / file_editor tools to understand it.\n"
        "  2. Make the SMALLEST possible code change that fixes the bug — revert buggy behavior, do NOT refactor.\n"
        "  3. Do NOT edit any test files.\n"
        "  4. You may run pytest to verify your fix, but this is optional.\n"
        "  5. When your fix is in place, call the `finish` tool.\n"
        "Treat this as a non-interactive, headless task. Do not ask clarifying questions.\n"
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: _openhands_runner.py <task.json>", file=sys.stderr)
        return 2
    task = json.loads(Path(argv[1]).read_text())

    workdir = task["workdir"]
    model = task["model"]
    base_url = task.get("base_url") or None
    max_iteration = int(task.get("max_iteration", 30))
    result_file = Path(task["result_file"])
    api_key = os.environ.get("OH_API_KEY", "")

    result: dict = {"token_usage": {}, "iterations": 0, "error": None}
    try:
        # Lazy import so a bad install gets captured in result["error"].
        from openhands.sdk import LLM, Conversation
        from openhands.tools.preset.default import get_default_agent
        from pydantic import SecretStr

        llm_kwargs = dict(usage_id="main", model=model, api_key=SecretStr(api_key))
        if base_url:
            llm_kwargs["base_url"] = base_url
        llm = LLM(**llm_kwargs)
        agent = get_default_agent(llm=llm, cli_mode=True)

        conv = Conversation(
            agent=agent,
            workspace=workdir,
            max_iteration_per_run=max_iteration,
            delete_on_close=False,
            visualizer=None,  # silence the default rich visualizer
        )
        prompt = _build_task_prompt(
            task["problem_statement"], list(task.get("failing_tests", [])), workdir,
        )
        conv.send_message(prompt)
        conv.run()

        # Best-effort token-usage extraction via conversation stats.
        try:
            stats = conv.state.stats if hasattr(conv, "state") else None
            if stats is None:
                stats = getattr(conv, "conversation_stats", None)
            if stats is not None:
                # Stats object has .accumulated_token_usage on recent versions
                acc = getattr(stats, "accumulated_token_usage", None) or getattr(stats, "total_token_usage", None)
                if acc is not None:
                    result["token_usage"] = {
                        "input": int(getattr(acc, "prompt_tokens", 0) or 0),
                        "output": int(getattr(acc, "completion_tokens", 0) or 0),
                    }
        except Exception as e:
            print(f"[runner] token-usage extraction failed: {e!r}", file=sys.stderr)

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc(file=sys.stderr)

    try:
        result_file.write_text(json.dumps(result))
    except Exception as e:
        print(f"[runner] could not write result file: {e!r}", file=sys.stderr)

    return 0 if result["error"] is None else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
