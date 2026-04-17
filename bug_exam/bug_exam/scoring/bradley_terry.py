"""Bradley-Terry MLE with bootstrap confidence intervals.

Given pairwise win/loss observations across solvers, fit ability scores:
    P(i beats j) = exp(beta_i) / (exp(beta_i) + exp(beta_j))

We use the iterative MM (minorization-maximization) update of
Hunter (2004) — simple, monotone, no external dependencies.

Ties (both solvers pass or both fail on the same exam) are half-counted:
    each gets 0.5 wins in the w[i,j] matrix.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass
class BTResult:
    solvers: list[str]
    ratings: dict[str, float]       # centered log-abilities
    ci_lo: dict[str, float]
    ci_hi: dict[str, float]
    n_pairs: int


def _build_matrix(solvers: list[str], pair_outcomes: Iterable[tuple[str, str, float]]) -> np.ndarray:
    """Outcomes: (i_name, j_name, w_ij) where w_ij in {0, 0.5, 1}."""
    idx = {s: k for k, s in enumerate(solvers)}
    n = len(solvers)
    w = np.zeros((n, n), dtype=float)
    for a, b, s in pair_outcomes:
        if a not in idx or b not in idx or a == b:
            continue
        w[idx[a], idx[b]] += s
        w[idx[b], idx[a]] += 1.0 - s
    return w


def _mm_fit(w: np.ndarray, max_iter: int = 500, tol: float = 1e-7) -> np.ndarray:
    n = w.shape[0]
    p = np.ones(n)
    # Iterative MM update (Hunter 2004).
    for _ in range(max_iter):
        p_new = np.zeros(n)
        for i in range(n):
            num = 0.0
            denom = 0.0
            for j in range(n):
                if i == j:
                    continue
                num += w[i, j]
                if (p[i] + p[j]) > 0:
                    denom += (w[i, j] + w[j, i]) / (p[i] + p[j])
            p_new[i] = num / denom if denom > 0 else p[i]
        p_new = np.where(p_new <= 0, 1e-9, p_new)
        # normalize to a fixed scale to avoid drift
        p_new = p_new * (len(p_new) / p_new.sum())
        if np.max(np.abs(p_new - p)) < tol:
            p = p_new
            break
        p = p_new
    # return log-abilities, centered
    beta = np.log(p)
    beta -= beta.mean()
    return beta


def fit(
    solvers: list[str],
    pair_outcomes: list[tuple[str, str, float]],
    *,
    bootstrap: int = 200,
    seed: int = 0,
) -> BTResult:
    if not solvers:
        return BTResult(solvers=[], ratings={}, ci_lo={}, ci_hi={}, n_pairs=0)
    w = _build_matrix(solvers, pair_outcomes)
    beta = _mm_fit(w)
    ratings = {s: float(beta[i]) for i, s in enumerate(solvers)}

    # Bootstrap: resample pair_outcomes with replacement, refit.
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {s: [] for s in solvers}
    if bootstrap and pair_outcomes:
        n_out = len(pair_outcomes)
        for _ in range(bootstrap):
            resampled = [pair_outcomes[rng.randrange(n_out)] for _ in range(n_out)]
            w_b = _build_matrix(solvers, resampled)
            try:
                b = _mm_fit(w_b)
                for i, s in enumerate(solvers):
                    samples[s].append(float(b[i]))
            except Exception:
                continue
    ci_lo: dict[str, float] = {}
    ci_hi: dict[str, float] = {}
    for s in solvers:
        arr = samples[s]
        if len(arr) < 20:
            ci_lo[s] = ratings[s]
            ci_hi[s] = ratings[s]
        else:
            arr_sorted = sorted(arr)
            lo = arr_sorted[int(0.025 * len(arr_sorted))]
            hi = arr_sorted[int(0.975 * len(arr_sorted))]
            ci_lo[s] = lo
            ci_hi[s] = hi
    return BTResult(
        solvers=list(solvers), ratings=ratings, ci_lo=ci_lo, ci_hi=ci_hi,
        n_pairs=len(pair_outcomes),
    )


def build_pairwise_from_grades(grades: list[dict]) -> list[tuple[str, str, float]]:
    """Given a list of grade dicts {exam_id, solver_name, final_passed},
    emit all within-exam pairwise outcomes. final_passed is 0/1/True/False.
    """
    by_exam: dict[str, list[tuple[str, bool]]] = {}
    for g in grades:
        by_exam.setdefault(g["exam_id"], []).append((g["solver_name"], bool(g["final_passed"])))
    pairs: list[tuple[str, str, float]] = []
    for exam_id, rows in by_exam.items():
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, pa = rows[i]
                b, pb = rows[j]
                if pa and not pb:
                    pairs.append((a, b, 1.0))
                elif pb and not pa:
                    pairs.append((a, b, 0.0))
                else:
                    pairs.append((a, b, 0.5))
    return pairs
