"""Patch-hash dedup.

Exam uniqueness is enforced by a sha256 over the injection_patch text. The
sqlite `exams` table has a UNIQUE index on patch_hash, so this module is
mostly a convenience: it computes the hash and checks the DB before we spend
compute running validation.
"""
from __future__ import annotations

import hashlib

from ..db import Database


def patch_hash(patch_text: str) -> str:
    return hashlib.sha256(patch_text.encode("utf-8")).hexdigest()


def is_duplicate(db: Database, patch_text: str) -> bool:
    return db.exam_patch_hash_exists(patch_hash(patch_text))
