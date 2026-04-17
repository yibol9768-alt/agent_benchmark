"""Item Response Theory scoring — Phase 2+ placeholder.

Fits a joint model with per-exam difficulty and per-solver ability from
binary correct/incorrect outcomes. Phase 1 ships a stub so higher layers
can import the module without churn.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IRTResult:
    solver_ability: dict[str, float]
    exam_difficulty: dict[str, float]


def fit_irt(grades: list[dict]) -> IRTResult:
    """Phase 1 stub — returns empty parameters."""
    return IRTResult(solver_ability={}, exam_difficulty={})
