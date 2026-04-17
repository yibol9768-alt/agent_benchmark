"""AST-diff helpers.

Phase 1 is Python-only and uses the stdlib `ast` module. A future Phase 3
version will wrap tree-sitter for the other 5 languages.

This module exposes two primitives:
  diff_files_from_patch(patch_text) -> list[str]
      Extract the set of a/path (before) files touched by a unified diff.

  apply_patch_to_workdir(patch_text, workdir) -> bool
      Best-effort `git apply` in an existing repo checkout. Used by the
      validator to materialize the buggy state so `ast` can see it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)", re.MULTILINE)


def files_touched(patch_text: str) -> list[str]:
    """Return the list of relative file paths touched by a unified diff."""
    seen: list[str] = []
    out: set[str] = set()
    for m in _DIFF_HEADER_RE.finditer(patch_text or ""):
        p = m.group("a")
        if p not in out:
            out.add(p)
            seen.append(p)
    return seen


def apply_patch(patch_text: str, workdir: Path) -> tuple[bool, str]:
    """git apply the patch in workdir; returns (ok, stderr)."""
    if not patch_text.strip():
        return False, "empty patch"
    p = workdir / ".bug_exam_patch.diff"
    p.write_text(patch_text)
    try:
        res = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(p.name)],
            cwd=str(workdir), capture_output=True, text=True, timeout=60,
        )
    finally:
        try:
            p.unlink()
        except Exception:
            pass
    return res.returncode == 0, res.stderr
