"""Contamination stratification for the leaderboard.

Each repo carries a `post_cutoff` flag (computed at harvest time from
created_at >= harvester.filters.created_after). The scoring/stratify module
uses this flag to report Elo separately for "post-cutoff" vs. "all".

This module adds two auxiliary signals:
  - `commit_majority_after_cutoff`: fraction of commits after the cutoff date
  - `stars_after_cutoff`: star count growth after the cutoff (proxy for recent
    popularity; rough but cheap)

In Phase 5 we also cross-reference against Stack v2 release dates — that
requires a per-release mapping not yet checked in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ContaminationFlags:
    post_cutoff_created: bool
    commit_majority_after_cutoff: bool
    stars_after_cutoff: int | None


def compute_post_cutoff_flag(created_at: datetime, cutoff: datetime) -> bool:
    return created_at >= cutoff


def stratify_groups(post_cutoff: bool) -> list[str]:
    """Return the stratification bucket names a given exam belongs to."""
    groups = ["all"]
    if post_cutoff:
        groups.append("post_cutoff")
    else:
        groups.append("pre_cutoff")
    return groups
