from __future__ import annotations

from benchmark_suite.models import FailureType, TaskSpec


def evaluate_response(task: TaskSpec, final_output: str) -> tuple[bool, float, FailureType]:
    must_contain = [str(item).lower() for item in task.expected.get("must_contain", [])]
    normalized = final_output.lower()
    if not must_contain:
        return bool(final_output.strip()), 1.0 if final_output.strip() else 0.0, FailureType.NONE

    matched = sum(1 for token in must_contain if token in normalized)
    score = matched / len(must_contain)
    resolved = matched == len(must_contain)
    failure = FailureType.NONE if resolved else FailureType.VALIDATION_ERROR
    return resolved, score, failure
