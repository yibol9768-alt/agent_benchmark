from __future__ import annotations

import time

from benchmark_suite.agent_adapters.base import AgentAdapter
from benchmark_suite.evaluators import evaluate_response
from benchmark_suite.models import FailureType, RunResult, TaskSpec


def run_tasks(tasks: list[TaskSpec], agent: AgentAdapter) -> list[RunResult]:
    results: list[RunResult] = []
    for task in tasks:
        started = time.perf_counter()
        try:
            response = agent.run_task(task)
            resolved, score, failure_type = evaluate_response(task, response.final_output)
        except Exception as exc:  # noqa: BLE001
            wall_time = time.perf_counter() - started
            results.append(
                RunResult(
                    task_id=task.task_id,
                    benchmark_family=task.benchmark_family,
                    agent_name=agent.name,
                    model_name=agent.model_name,
                    resolved=False,
                    score_raw=0.0,
                    steps=0,
                    wall_time_sec=wall_time,
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0.0,
                    tool_calls=0,
                    failure_type=FailureType.OTHER,
                    final_output="",
                    trace=[],
                    metadata={"error": str(exc)},
                )
            )
            continue

        wall_time = time.perf_counter() - started
        results.append(
            RunResult(
                task_id=task.task_id,
                benchmark_family=task.benchmark_family,
                agent_name=agent.name,
                model_name=agent.model_name,
                resolved=resolved,
                score_raw=score,
                steps=response.steps,
                wall_time_sec=wall_time,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                cost_usd=response.cost_usd,
                tool_calls=response.tool_calls,
                failure_type=failure_type,
                final_output=response.final_output,
                trace=response.trace,
                metadata=response.metadata,
            )
        )
    return results
