"""Shared helpers for SWE-Bench Pro scripts.

Used by scripts/run_swebench_pro_batch.py and scripts/run_contamination_probe.py.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def git_apply_check(workdir: Path, diff: str) -> tuple[bool, str]:
    """Check whether a unified diff applies cleanly to workdir."""
    p = workdir / ".apply_check.diff"
    p.write_text(diff)
    try:
        res = subprocess.run(
            ["git", "apply", "--check", "--recount", "--whitespace=nowarn", p.name],
            cwd=str(workdir), capture_output=True, text=True, timeout=60,
        )
        return res.returncode == 0, res.stderr.strip()[-500:]
    finally:
        p.unlink(missing_ok=True)


def git_reset(workdir: Path, base_commit: str) -> None:
    """Hard-reset workdir to base_commit and clean untracked files."""
    subprocess.run(["git", "reset", "--hard", base_commit],
                   cwd=str(workdir), capture_output=True, timeout=60)
    subprocess.run(["git", "clean", "-fd"], cwd=str(workdir), capture_output=True, timeout=60)


def prepare_buggy_workdir(src: Path, dst: Path, base_commit: str, injection_diff: str) -> None:
    """Clone src to dst, checkout base_commit, apply injection diff."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", str(src), str(dst)],
                   capture_output=True, text=True, check=True, timeout=180)
    subprocess.run(["git", "checkout", "--quiet", base_commit],
                   cwd=str(dst), capture_output=True, text=True, check=True, timeout=60)
    if not injection_diff.strip():
        return
    p = dst / ".inject.diff"
    p.write_text(injection_diff)
    res = subprocess.run(
        ["git", "apply", "--recount", "--whitespace=nowarn", p.name],
        cwd=str(dst), capture_output=True, text=True, timeout=60,
    )
    p.unlink(missing_ok=True)
    if res.returncode != 0:
        raise RuntimeError(f"failed to apply injection: {res.stderr[-400:]}")


def solver_cfg(name: str) -> dict:
    """Load a solver's config from configs/solvers.yaml."""
    cfg = yaml.safe_load((ROOT / "configs" / "solvers.yaml").read_text())
    return cfg["solvers"][name]


def test_in_selected_files(test_id: str, selected_test_files: list[str]) -> bool:
    """Check if a pytest nodeid belongs to one of the selected test files."""
    base = test_id.split("::", 1)[0].lstrip("./")
    return any(base == f.lstrip("./") or base.endswith("/" + f.lstrip("./"))
               for f in selected_test_files)
