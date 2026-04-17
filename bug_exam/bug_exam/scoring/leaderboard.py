"""Leaderboard aggregation.

Reads grades from the sqlite DB, fits Bradley-Terry + streaming Elo, computes
stratified pass rates, and emits a JSON + HTML report.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..db import Database
from ..schema import ExamStatus, LeaderboardEntry
from .bradley_terry import build_pairwise_from_grades, fit as bt_fit
from .elo import batch_update
from .stratify import solve_rate


def build_leaderboard(db: Database) -> dict[str, Any]:
    grades_raw = db.list_grades()
    grades = [g.model_dump() for g in grades_raw]
    if not grades:
        return {"solvers": [], "n_runs": 0, "n_exams": 0}

    exams = {e.instance_id: e for e in db.list_exams(ExamStatus.FROZEN)}
    # Fallback: include validated too if nothing frozen
    if not exams:
        exams = {e.instance_id: e for e in db.list_exams(ExamStatus.VALIDATED)}

    exam_band = {eid: e.difficulty_band for eid, e in exams.items()}
    exam_lang = {eid: e.language.value for eid, e in exams.items()}
    exam_post_cutoff = {eid: e.post_cutoff for eid, e in exams.items()}

    solvers = sorted({g["solver_name"] for g in grades})
    pairs = build_pairwise_from_grades(grades)
    bt = bt_fit(solvers, pairs, bootstrap=200)
    elo = batch_update(pairs)

    entries: list[LeaderboardEntry] = []
    for s in solvers:
        rows = [g for g in grades if g["solver_name"] == s]
        rate = solve_rate(rows)
        by_band: dict[str, float] = defaultdict(lambda: 0.0)
        band_totals: dict[str, int] = defaultdict(int)
        band_hits: dict[str, int] = defaultdict(int)
        by_lang_totals: dict[str, int] = defaultdict(int)
        by_lang_hits: dict[str, int] = defaultdict(int)
        for g in rows:
            band = exam_band.get(g["exam_id"], "unknown")
            band_totals[band] += 1
            if g["final_passed"]:
                band_hits[band] += 1
            lang = exam_lang.get(g["exam_id"], "unknown")
            by_lang_totals[lang] += 1
            if g["final_passed"]:
                by_lang_hits[lang] += 1
        for band, tot in band_totals.items():
            by_band[band] = (band_hits[band] / tot) if tot else 0.0
        by_lang = {lang: (by_lang_hits[lang] / tot) for lang, tot in by_lang_totals.items() if tot}

        entries.append(LeaderboardEntry(
            solver_name=s,
            bt_rating=bt.ratings.get(s, 0.0),
            bt_ci_lo=bt.ci_lo.get(s, 0.0),
            bt_ci_hi=bt.ci_hi.get(s, 0.0),
            elo_rating=elo.get(s),
            pass_rate_overall=rate,
            pass_rate_by_band=dict(by_band),
            pass_rate_by_language=by_lang,
            n_runs=len(rows),
        ))
    entries.sort(key=lambda e: e.bt_rating, reverse=True)

    # Stratified (post_cutoff vs all) BT ratings
    post_grades = [g for g in grades if exam_post_cutoff.get(g["exam_id"], False)]
    post_pairs = build_pairwise_from_grades(post_grades)
    bt_post = bt_fit(solvers, post_pairs, bootstrap=100) if post_pairs else None

    payload = {
        "n_runs": len(grades),
        "n_exams": len({g["exam_id"] for g in grades}),
        "n_pairs": bt.n_pairs,
        "solvers": [e.model_dump() for e in entries],
        "stratified": {
            "post_cutoff_bt": bt_post.ratings if bt_post else None,
            "post_cutoff_n_pairs": bt_post.n_pairs if bt_post else 0,
        },
    }
    return payload


def write_leaderboard(db: Database, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_leaderboard(db)
    out_path = out_dir / "leaderboard.json"
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path
