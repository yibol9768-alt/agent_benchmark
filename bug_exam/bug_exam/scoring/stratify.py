"""Stratified reporting: split the run corpus by cut-off, language, and
difficulty band so the leaderboard can surface contamination effects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class StrataSplit:
    name: str                     # "all" | "post_cutoff" | "pre_cutoff" | band id
    grade_rows: list[dict]


def split_by_cutoff(grades: list[dict], exam_post_cutoff: dict[str, bool]) -> list[StrataSplit]:
    all_grades = list(grades)
    post = [g for g in grades if exam_post_cutoff.get(g["exam_id"], False)]
    pre = [g for g in grades if not exam_post_cutoff.get(g["exam_id"], False)]
    return [
        StrataSplit("all", all_grades),
        StrataSplit("post_cutoff", post),
        StrataSplit("pre_cutoff", pre),
    ]


def split_by_band(grades: list[dict], exam_band: dict[str, str]) -> list[StrataSplit]:
    buckets: dict[str, list[dict]] = {}
    for g in grades:
        buckets.setdefault(exam_band.get(g["exam_id"], "unknown"), []).append(g)
    return [StrataSplit(name=name, grade_rows=rows) for name, rows in buckets.items()]


def solve_rate(rows: Iterable[dict], solver_name: str | None = None) -> float:
    total = 0
    hits = 0
    for g in rows:
        if solver_name and g["solver_name"] != solver_name:
            continue
        total += 1
        if g["final_passed"]:
            hits += 1
    return hits / total if total else 0.0
