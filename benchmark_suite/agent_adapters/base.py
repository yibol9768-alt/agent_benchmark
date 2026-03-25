from __future__ import annotations

from abc import ABC, abstractmethod

from benchmark_suite.models import AgentResponse, TaskSpec


class AgentAdapter(ABC):
    name: str
    model_name: str

    @abstractmethod
    def run_task(self, task: TaskSpec) -> AgentResponse:
        raise NotImplementedError
