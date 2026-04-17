"""Aider adapter.

Runs the aider CLI against a workdir. Aider's --message-file + --yes mode is
non-interactive and produces diffs we can read via git diff afterwards.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from ..schema import ExamInstance, SolverResult


class AiderSolver:
    name = "aider"

    def __init__(self, model: str = "claude-opus-4-6", timeout_s: int = 1800):
        self.model = model
        self.default_timeout_s = timeout_s

    def solve(self, exam: ExamInstance, workdir: Path, timeout_s: int | None = None) -> SolverResult:
        started = time.time()
        timeout_s = timeout_s or self.default_timeout_s

        aider_bin = shutil.which("aider")
        if not aider_bin:
            return SolverResult(
                solver_name=self.name, exam_id=exam.instance_id, patch="",
                wall_clock_s=time.time() - started, errored=True,
                error_message="aider CLI not found in PATH (pip install aider-chat)",
            )

        # Snapshot pre-state
        try:
            subprocess.run(["git", "add", "-A"], cwd=str(workdir), capture_output=True, timeout=30)
            subprocess.run(
                ["git", "commit", "-m", "bug_exam pre-solve snapshot", "--allow-empty"],
                cwd=str(workdir), capture_output=True, timeout=30,
                env={**os.environ,
                     "GIT_AUTHOR_NAME": "bug-exam", "GIT_AUTHOR_EMAIL": "b@e.x",
                     "GIT_COMMITTER_NAME": "bug-exam", "GIT_COMMITTER_EMAIL": "b@e.x"},
            )
        except Exception:
            pass

        msg_file = workdir / ".bug_exam_msg.txt"
        msg_file.write_text(
            exam.problem_statement
            + "\n\nPlease locate and fix the bug. Make a minimal change that does not touch test files."
        )

        cmd = [
            aider_bin,
            "--model", self._map_model_name(),
            "--yes",
            "--no-auto-commits",
            "--no-pretty",
            "--message-file", str(msg_file),
        ]

        err = None
        try:
            res = subprocess.run(
                cmd, cwd=str(workdir), capture_output=True, text=True, timeout=timeout_s,
            )
            if res.returncode != 0:
                err = f"aider rc={res.returncode}: {res.stderr[-1500:]}"
        except subprocess.TimeoutExpired:
            err = "aider timed out"
        except Exception as e:
            err = f"aider error: {e}"

        patch = self._compute_diff(workdir)
        return SolverResult(
            solver_name=self.name, exam_id=exam.instance_id, patch=patch,
            wall_clock_s=time.time() - started,
            errored=err is not None, error_message=err,
        )

    def _map_model_name(self) -> str:
        # aider expects a specific nomenclature; map our canonical id.
        if "opus" in self.model:
            return "anthropic/claude-opus-4-6"
        if "sonnet" in self.model:
            return "anthropic/claude-sonnet-4-6"
        return self.model

    def _compute_diff(self, workdir: Path) -> str:
        try:
            res = subprocess.run(["git", "diff", "HEAD"], cwd=str(workdir),
                                 capture_output=True, text=True, timeout=60)
            return res.stdout
        except Exception:
            return ""
