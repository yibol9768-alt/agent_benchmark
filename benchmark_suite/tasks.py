from __future__ import annotations

from pathlib import Path

from benchmark_suite.io_utils import load_jsonl
from benchmark_suite.models import TaskSpec


def load_tasks(path: str | Path) -> list[TaskSpec]:
    return [TaskSpec.from_dict(item) for item in load_jsonl(path)]


def validate_tasks(path: str | Path) -> list[str]:
    errors: list[str] = []
    raw_items = load_jsonl(path)
    seen: set[str] = set()
    for idx, item in enumerate(raw_items, start=1):
        try:
            task = TaskSpec.from_dict(item)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"line {idx}: {exc}")
            continue
        if task.task_id in seen:
            errors.append(f"line {idx}: duplicate task_id {task.task_id}")
        seen.add(task.task_id)
    return errors
