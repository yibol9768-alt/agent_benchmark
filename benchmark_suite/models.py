from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StringEnum(str, Enum):
    pass


class BenchmarkFamily(StringEnum):
    SWE = "swe"
    WEB = "web"
    TOOL = "tool"


class FailureType(StringEnum):
    NONE = "none"
    PLANNING_ERROR = "planning_error"
    NAVIGATION_ERROR = "navigation_error"
    TOOL_USE_ERROR = "tool_use_error"
    ENVIRONMENT_ERROR = "environment_error"
    VALIDATION_ERROR = "validation_error"
    CONTEXT_OVERFLOW = "context_overflow"
    STUCK_LOOP = "stuck_loop"
    OTHER = "other"


@dataclass
class Budget:
    max_steps: int = 16
    max_runtime_sec: int = 300

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Budget":
        if not data:
            return cls()
        return cls(
            max_steps=int(data.get("max_steps", 16)),
            max_runtime_sec=int(data.get("max_runtime_sec", 300)),
        )


@dataclass
class TaskSpec:
    task_id: str
    benchmark_family: BenchmarkFamily
    title: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    budget: Budget = field(default_factory=Budget)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSpec":
        return cls(
            task_id=str(data["task_id"]),
            benchmark_family=BenchmarkFamily(data["benchmark_family"]),
            title=str(data.get("title") or data["task_id"]),
            prompt=str(data["prompt"]),
            metadata=dict(data.get("metadata", {})),
            expected=dict(data.get("expected", {})),
            budget=Budget.from_dict(data.get("budget")),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["benchmark_family"] = self.benchmark_family.value
        return payload


@dataclass
class AgentResponse:
    final_output: str
    steps: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    trace: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentResponse":
        return cls(
            final_output=str(data.get("final_output", "")),
            steps=int(data.get("steps", 0)),
            tool_calls=int(data.get("tool_calls", 0)),
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            trace=[str(item) for item in data.get("trace", [])],
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    task_id: str
    benchmark_family: BenchmarkFamily
    agent_name: str
    model_name: str
    resolved: bool
    score_raw: float
    steps: int
    wall_time_sec: float
    tokens_in: int
    tokens_out: int
    cost_usd: float
    tool_calls: int
    failure_type: FailureType
    final_output: str
    trace: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["benchmark_family"] = self.benchmark_family.value
        payload["failure_type"] = self.failure_type.value
        return payload
