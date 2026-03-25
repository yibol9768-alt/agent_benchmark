from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from benchmark_suite.agent_adapters.base import AgentAdapter
from benchmark_suite.models import AgentResponse, TaskSpec


class CodexCommandAdapter(AgentAdapter):
    name = "codex-cmd"
    model_name = "gpt-5.4"

    def __init__(self, command: str = "codex") -> None:
        self.command = command

    def run_task(self, task: TaskSpec) -> AgentResponse:
        prompt = (
            "You are being evaluated on a benchmark task.\n"
            "Do not modify files unless the task explicitly requires it.\n"
            "Answer directly and concisely.\n"
            f"Benchmark family: {task.benchmark_family.value}\n"
            f"Title: {task.title}\n"
            f"Task: {task.prompt}\n"
            f"Expected hints: {json.dumps(task.expected, ensure_ascii=False)}\n"
            "Return the final answer only."
        )
        with tempfile.NamedTemporaryFile(prefix="codex_last_", suffix=".txt", delete=False) as handle:
            output_path = Path(handle.name)

        cmd = [
            self.command,
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--output-last-message",
            str(output_path),
            "-C",
            str(Path.cwd()),
            prompt,
        ]
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=task.budget.max_runtime_sec,
            check=False,
        )
        try:
            if completed.returncode != 0:
                raise RuntimeError(
                    f"codex exec failed with code {completed.returncode}: {completed.stderr.strip()}"
                )
            final_output = output_path.read_text(encoding="utf-8").strip()
            return AgentResponse(
                final_output=final_output,
                steps=1,
                tool_calls=0,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                trace=["codex exec"],
                metadata={"stdout": completed.stdout[-2000:]},
            )
        finally:
            output_path.unlink(missing_ok=True)
