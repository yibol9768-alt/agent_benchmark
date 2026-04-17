"""Streaming Elo, for the live leaderboard.

Not the headline metric (that's BT) but convenient for incremental updates
while the benchmark is still running.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EloState:
    ratings: dict[str, float] = field(default_factory=dict)
    k: float = 16.0
    default: float = 1500.0

    def get(self, name: str) -> float:
        return self.ratings.get(name, self.default)

    def update_pair(self, a: str, b: str, outcome: float) -> None:
        """outcome = 1 if a wins, 0 if b wins, 0.5 for tie."""
        ra = self.get(a)
        rb = self.get(b)
        ea = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
        eb = 1.0 - ea
        self.ratings[a] = ra + self.k * (outcome - ea)
        self.ratings[b] = rb + self.k * ((1.0 - outcome) - eb)


def batch_update(pairs: list[tuple[str, str, float]], k: float = 16.0) -> EloState:
    state = EloState(k=k)
    for a, b, o in pairs:
        state.update_pair(a, b, o)
    return state
