from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from benchmark_suite.agent_adapters.base import AgentAdapter
from benchmark_suite.models import AgentResponse, TaskSpec


class BareLLMAdapter(AgentAdapter):
    name = "bare-llm"

    def __init__(self) -> None:
        self.base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
        self.api_key = os.environ["OPENAI_API_KEY"]
        self.model_name = os.environ["OPENAI_MODEL"]

    def run_task(self, task: TaskSpec) -> AgentResponse:
        prompt = (
            "You are a baseline model runner. "
            "Return a concise answer for the task. "
            "You do not have tool access. "
            f"Task family: {task.benchmark_family.value}\n"
            f"Title: {task.title}\n"
            f"Task: {task.prompt}\n"
            f"Expected hints: {json.dumps(task.expected, ensure_ascii=False)}"
        )
        payload = {
            "model": self.model_name,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=task.budget.max_runtime_sec) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        choice = raw["choices"][0]["message"]["content"]
        usage = raw.get("usage", {})
        return AgentResponse(
            final_output=choice,
            steps=1,
            tool_calls=0,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            cost_usd=0.0,
            trace=["single completion"],
            metadata={"endpoint": self.base_url},
        )
