from __future__ import annotations

from benchmark_suite.agent_adapters.base import AgentAdapter
from benchmark_suite.models import AgentResponse, BenchmarkFamily, TaskSpec


class MockAgentAdapter(AgentAdapter):
    name = "mock"
    model_name = "mock-model"

    def run_task(self, task: TaskSpec) -> AgentResponse:
        must_contain = task.expected.get("must_contain", [])
        joined = " ".join(str(item) for item in must_contain) or "mock-answer"
        return AgentResponse(
            final_output=f"mock resolved {task.benchmark_family.value}: {joined}",
            steps=min(task.budget.max_steps, max(1, len(must_contain) + 1)),
            tool_calls=1 if task.benchmark_family != BenchmarkFamily.SWE else 2,
            tokens_in=120,
            tokens_out=45,
            cost_usd=0.0005,
            trace=["read task", "generated answer"],
            metadata={"adapter": self.name},
        )
