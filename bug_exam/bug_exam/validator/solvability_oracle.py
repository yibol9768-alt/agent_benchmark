"""Solvability oracle — Phase 2 gate.

Runs a strong-model solver against the candidate bug and confirms the bug is
actually fixable. Unsolvable bugs (e.g., the symptom can't be reproduced
without the injection patch itself) are rejected.

Phase 1 ships a no-op stub so the import graph is complete.
"""
from __future__ import annotations

from ..schema import BreakPlan, ExamInstance


def confirm_solvable(exam: ExamInstance, repo_dir, image_tag: str) -> bool:
    """Return True if the oracle solver can produce a passing fix.

    Phase 1 stub: always True. Phase 2 will implement this using the same
    claude_direct adapter as the leaderboard solvers but with full-repo read
    access.
    """
    return True
