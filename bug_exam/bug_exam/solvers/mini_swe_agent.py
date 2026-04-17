"""mini-swe-agent adapter.

Shells out to the mini-swe-agent CLI already vendored under
`benchmarks/SWE-bench_Pro-os/mini-swe-agent/`. Runs the agent on a fresh
checkout with the bug applied, then diffs the working tree.

This is deliberately a thin shim: the upstream agent handles prompting,
tool loop, etc. We only wire I/O.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ..schema import ExamInstance, SolverResult


MINI_SWE_REPO = Path(__file__).resolve().parents[3] / "SWE-bench_Pro-os" / "mini-swe-agent"


class MiniSweAgentSolver:
    name = "mini_swe_agent"

    def __init__(
        self,
        model: str = "claude-opus-4-6",
        config_path: str | None = None,
        timeout_s: int = 1800,
    ):
        self.model = model
        self.config_path = config_path
        self.default_timeout_s = timeout_s

    def solve(self, exam: ExamInstance, workdir: Path, timeout_s: int | None = None) -> SolverResult:
        started = time.time()
        timeout_s = timeout_s or self.default_timeout_s

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

        # Write task file
        task_file = workdir / ".bug_exam_task.txt"
        task_file.write_text(exam.problem_statement)

        cmd = [
            "python", "-m", "minisweagent.run.mini",
            "--task-file", str(task_file),
            "--model", self.model,
            "--cwd", str(workdir),
        ]
        if self.config_path:
            cmd += ["--config", self.config_path]

        env = {**os.environ, "PYTHONPATH": str(MINI_SWE_REPO / "src")}
        err = None
        try:
            res = subprocess.run(cmd, cwd=str(MINI_SWE_REPO), capture_output=True, text=True,
                                 timeout=timeout_s, env=env)
            if res.returncode != 0:
                err = f"mini-swe-agent rc={res.returncode}: {res.stderr[-1500:]}"
        except subprocess.TimeoutExpired:
            err = "mini-swe-agent timed out"
        except FileNotFoundError as e:
            err = f"mini-swe-agent not installed: {e}"

        patch = self._compute_diff(workdir)
        return SolverResult(
            solver_name=self.name, exam_id=exam.instance_id, patch=patch,
            wall_clock_s=time.time() - started,
            errored=err is not None, error_message=err,
        )

    def _compute_diff(self, workdir: Path) -> str:
        try:
            res = subprocess.run(["git", "diff", "HEAD"], cwd=str(workdir),
                                 capture_output=True, text=True, timeout=60)
            return res.stdout
        except Exception:
            return ""
