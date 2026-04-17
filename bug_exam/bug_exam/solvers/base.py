"""SolverAdapter protocol + shared helpers.

Every solver adapter presents the same interface so the orchestrator can
treat them uniformly:

    class MySolver:
        name = "my_solver"
        def solve(self, exam: ExamInstance, workdir: Path, timeout_s: int) -> SolverResult: ...

The adapter is responsible for:
  - running its agent/pipeline against the buggy repo state
  - collecting the agent's final patch
  - filling in wall_clock_s, token_usage (best-effort), errored, error_message
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..schema import ExamInstance, SolverResult

log = logging.getLogger(__name__)


@runtime_checkable
class SolverAdapter(Protocol):
    name: str

    def solve(self, exam: ExamInstance, workdir: Path, timeout_s: int) -> SolverResult:
        ...


def load_solver(config: dict) -> SolverAdapter:
    """Instantiate a solver from its configs/solvers.yaml entry."""
    module = importlib.import_module(config["module"])
    cls = getattr(module, config["class"])
    kwargs = {k: v for k, v in config.items() if k not in ("module", "class", "enabled")}
    return cls(**kwargs)
