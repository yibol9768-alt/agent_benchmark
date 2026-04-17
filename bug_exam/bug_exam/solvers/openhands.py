"""OpenHands (v1.x SDK) adapter.

Runs the OpenHands agent on the buggy repo in a *separate* Python interpreter
(OpenHands requires Python >=3.12; bug_exam's own venv might be older).
A wrapper script drives the OpenHands SDK's ``Conversation`` with a ``LocalWorkspace``
pointing at the solver workdir, configured to talk to the same LLM
(GLM-5.1 via Anthropic-compat by default).

Why a subprocess?
  - OpenHands' dependency closure is huge (playwright, litellm, browser-use, ...)
    and requires 3.12; we don't want to force it onto the bug_exam venv.
  - Keeps the adapter lazy/optional: if OpenHands isn't installed, imports in the
    parent process don't fail. Unit tests still pass.

Env to configure which interpreter / wrapper to use:
  BUG_EXAM_OPENHANDS_PYTHON        path to the python that has ``openhands-ai``.
                                   default: /root/openhands_venv/bin/python
  BUG_EXAM_OPENHANDS_WRAPPER       path to wrapper script (default: adjacent to this file).

LLM plumbing reuses ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / ANTHROPIC_MODEL,
exactly like ``claude_direct``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from ..schema import ExamInstance, SolverResult

log = logging.getLogger(__name__)

_WRAPPER_DEFAULT = Path(__file__).resolve().parent / "_openhands_runner.py"
_PYTHON_DEFAULT = "/root/openhands_venv/bin/python"


class OpenHandsSolver:
    name = "openhands"

    def __init__(
        self,
        model: str | None = None,
        max_turns: int = 30,
        timeout_s: int = 1800,
        python_path: str | None = None,
        wrapper_path: str | None = None,
        base_url: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        **_: object,
    ):
        self.model = model
        self.max_turns = max_turns
        self.default_timeout_s = timeout_s
        self.python_path = python_path or os.environ.get(
            "BUG_EXAM_OPENHANDS_PYTHON", _PYTHON_DEFAULT
        )
        self.wrapper_path = wrapper_path or os.environ.get(
            "BUG_EXAM_OPENHANDS_WRAPPER", str(_WRAPPER_DEFAULT)
        )
        self.base_url = base_url
        self.api_key_env = api_key_env

    # ------------------------------------------------------------------
    def solve(self, exam: ExamInstance, workdir: Path, timeout_s: int | None = None) -> SolverResult:
        started = time.time()
        timeout_s = timeout_s or self.default_timeout_s

        # Pre-solve snapshot so ``git diff HEAD`` captures agent edits.
        git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "bug-exam", "GIT_AUTHOR_EMAIL": "b@e.x",
            "GIT_COMMITTER_NAME": "bug-exam", "GIT_COMMITTER_EMAIL": "b@e.x",
        }
        try:
            subprocess.run(["git", "add", "-A"], cwd=str(workdir), capture_output=True, timeout=30)
            subprocess.run(
                ["git", "commit", "-m", "bug_exam pre-solve snapshot", "--allow-empty"],
                cwd=str(workdir), capture_output=True, timeout=30, env=git_env,
            )
        except Exception:
            pass

        # Resolve model / api details from env defaults.
        model = self.model or os.environ.get("ANTHROPIC_MODEL", "glm-5.1")
        base_url = self.base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        api_key = os.environ.get(self.api_key_env) or os.environ.get("ANTHROPIC_AUTH_TOKEN") or ""
        if not api_key:
            return SolverResult(
                solver_name=self.name, exam_id=exam.instance_id, patch="",
                wall_clock_s=time.time() - started, errored=True,
                error_message=f"no LLM api key in env ({self.api_key_env})",
            )

        # Prepare task payload — the wrapper reads this.
        task_file = workdir / ".bug_exam_openhands_task.json"
        result_file = workdir / ".bug_exam_openhands_result.json"
        payload = {
            "problem_statement": exam.problem_statement,
            "failing_tests": list(exam.FAIL_TO_PASS[:5]),
            "workdir": str(workdir),
            "model": f"anthropic/{model}" if not model.startswith(("anthropic/", "openai/", "litellm_proxy/")) else model,
            "base_url": base_url,
            "max_iteration": self.max_turns,
            "result_file": str(result_file),
        }
        task_file.write_text(json.dumps(payload))

        env = {
            **os.environ,
            "OPENHANDS_SUPPRESS_BANNER": "1",
            "OH_API_KEY": api_key,
        }

        cmd = [self.python_path, self.wrapper_path, str(task_file)]
        err: str | None = None
        proc_stdout = ""
        proc_stderr = ""
        try:
            res = subprocess.run(
                cmd, cwd=str(workdir), capture_output=True, text=True,
                timeout=timeout_s, env=env,
            )
            proc_stdout = res.stdout or ""
            proc_stderr = res.stderr or ""
            if res.returncode != 0:
                err = f"openhands wrapper rc={res.returncode}: {proc_stderr[-1500:]}"
        except subprocess.TimeoutExpired:
            err = f"openhands timed out after {timeout_s}s"
        except FileNotFoundError as e:
            err = f"openhands python/wrapper not found: {e}"

        # Load wrapper-side result (tokens, iterations).
        tokens: dict[str, int] = {}
        wrapper_error: str | None = None
        if result_file.exists():
            try:
                info = json.loads(result_file.read_text())
                tokens = info.get("token_usage", {}) or {}
                wrapper_error = info.get("error")
            except Exception as e:
                log.warning("could not parse openhands result file: %r", e)

        patch = self._compute_diff(workdir)

        # Clean up transient files before diff-ing next run.
        for f in (task_file, result_file):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

        return SolverResult(
            solver_name=self.name,
            exam_id=exam.instance_id,
            patch=patch,
            wall_clock_s=time.time() - started,
            token_usage=tokens,
            errored=err is not None or wrapper_error is not None,
            error_message=err or wrapper_error,
        )

    # ------------------------------------------------------------------
    def _compute_diff(self, workdir: Path) -> str:
        try:
            res = subprocess.run(
                ["git", "diff", "HEAD", "--", ".", ":(exclude).bug_exam_openhands_*"],
                cwd=str(workdir), capture_output=True, text=True, timeout=60,
            )
            return res.stdout
        except Exception as e:
            log.warning("git diff failed: %r", e)
            return ""
