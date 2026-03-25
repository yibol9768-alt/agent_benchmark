from __future__ import annotations

import json
import subprocess

from benchmark_suite.agent_adapters.base import AgentAdapter
from benchmark_suite.models import AgentResponse, TaskSpec


class CommandAgentAdapter(AgentAdapter):
    def __init__(self, name: str, command: str, model_name: str = "external-agent") -> None:
        self.name = name
        self.command = command
        self.model_name = model_name

    def run_task(self, task: TaskSpec) -> AgentResponse:
        payload = {"task": task.to_dict()}
        try:
            completed = subprocess.run(
                self.command,
                input=json.dumps(payload),
                text=True,
                shell=True,
                capture_output=True,
                timeout=task.budget.max_runtime_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Agent command timed out for {task.task_id}") from exc

        if completed.returncode != 0:
            raise RuntimeError(
                f"Agent command failed with code {completed.returncode}: {completed.stderr.strip()}"
            )

        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Agent command returned invalid JSON: {completed.stdout}") from exc
        return AgentResponse.from_dict(data)
