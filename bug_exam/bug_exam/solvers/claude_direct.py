"""Minimal LLM-direct baseline solver (name kept for backwards compat with
configs/solvers.yaml — the class is now provider-agnostic and the alias
`LLMDirectSolver` is exported too).

Gives the model a small tool set (read_file, list_dir, grep, run_tests,
apply_edit, emit_patch) and a repo checkout. No fancy scaffolding, no MCTS,
no retrieval beyond simple grep. This is the control arm — if the heavier
agents don't beat it, the scaffolding isn't earning its keep.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path

from ..llm import LLMClient, ToolDef, make_client
from ..schema import ExamInstance, SolverResult

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior software engineer tasked with fixing a bug
in a repository. You have a working checkout that you interact with through
tool calls. A bug has been introduced and the user will describe the
symptom. Your job is to locate and fix it using the smallest reasonable
patch.

Workflow:
  1. Read the problem statement
  2. Explore the repo with list_dir / grep / read_file
  3. Optionally run tests to reproduce
  4. Call apply_edit for each file you need to change
  5. Call emit_patch when you're done (this ends the session)

Rules:
  - Do NOT modify test files
  - Keep the fix minimal — revert the buggy behavior, do not refactor
  - The patch must make the failing tests pass without breaking the passing ones
"""


TOOLS: list[ToolDef] = [
    ToolDef(
        name="list_dir",
        description="List entries of a directory in the repo.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
            "required": [],
        },
    ),
    ToolDef(
        name="read_file",
        description="Read a file; returns numbered lines.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "default": 1},
                "end": {"type": "integer"},
            },
            "required": ["path"],
        },
    ),
    ToolDef(
        name="grep",
        description="Grep the repo for a regex pattern.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string", "default": "**/*.py"},
            },
            "required": ["pattern"],
        },
    ),
    ToolDef(
        name="run_tests",
        description="Run pytest with an optional test path filter.",
        input_schema={
            "type": "object",
            "properties": {"test_files": {"type": "string", "default": ""}},
            "required": [],
        },
    ),
    ToolDef(
        name="apply_edit",
        description="Replace a contiguous range of lines in a file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "start_line", "end_line", "new_text"],
        },
    ),
    ToolDef(
        name="emit_patch",
        description="Finalize and submit the solver's patch. Ends the session.",
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
]

TERMINAL = {"emit_patch"}


class ClaudeDirectSolver:
    """LLM-direct solver. Provider-neutral despite the name (kept for config
    compatibility). `model` can be a Claude or GLM model id depending on the
    provider picked by the factory."""

    name = "claude_direct"

    def __init__(
        self,
        model: str | None = None,
        max_turns: int = 30,
        timeout_s: int = 1800,
        provider: str | None = None,
        **_: object,
    ):
        self.model = model
        self.max_turns = max_turns
        self.default_timeout_s = timeout_s
        self.provider = provider

    # ------------------------------------------------------------------

    def _tool_handlers(self, workdir: Path) -> dict:
        def list_dir(args: dict) -> str:
            p = workdir / args.get("path", ".")
            if not p.is_dir():
                return f"error: {p} is not a directory"
            return "\n".join(sorted(
                (child.name + ("/" if child.is_dir() else ""))
                for child in p.iterdir() if not child.name.startswith(".")
            ))

        def read_file(args: dict) -> str:
            p = workdir / args["path"]
            if not p.is_file():
                return f"error: {args['path']} not found"
            lines = p.read_text(errors="replace").splitlines()
            s = max(1, args.get("start", 1))
            e = min(len(lines), args.get("end") or len(lines))
            return "\n".join(f"{i:6d}  {lines[i-1]}" for i in range(s, e + 1))

        def grep(args: dict) -> str:
            rx = re.compile(args["pattern"])
            hits: list[str] = []
            for path in workdir.glob(args.get("glob", "**/*.py")):
                if not path.is_file():
                    continue
                try:
                    for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                        if rx.search(line):
                            rel = path.relative_to(workdir)
                            hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                            if len(hits) >= 100:
                                return "\n".join(hits)
                except Exception:
                    continue
            return "\n".join(hits) if hits else "no matches"

        def run_tests(args: dict) -> str:
            files = args.get("test_files", "")
            cmd = ["python", "-m", "pytest", "--tb=short", "-q"]
            if files:
                cmd += files.split()
            try:
                res = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, timeout=600)
                return (res.stdout + "\n" + res.stderr)[-6000:]
            except subprocess.TimeoutExpired:
                return "error: test run timed out"

        def apply_edit(args: dict) -> str:
            p = workdir / args["path"]
            if not p.is_file():
                return f"error: {args['path']} not found"
            lines = p.read_text(errors="replace").splitlines(keepends=True)
            s = max(1, args["start_line"]) - 1
            e = min(len(lines), args["end_line"])
            new = args["new_text"]
            if not new.endswith("\n"):
                new += "\n"
            new_lines = lines[:s] + [new] + lines[e:]
            p.write_text("".join(new_lines))
            return f"ok: {args['path']} updated ({args['start_line']}..{args['end_line']})"

        return {
            "list_dir": list_dir,
            "read_file": read_file,
            "grep": grep,
            "run_tests": run_tests,
            "apply_edit": apply_edit,
        }

    # ------------------------------------------------------------------

    def solve(self, exam: ExamInstance, workdir: Path, timeout_s: int | None = None) -> SolverResult:
        started = time.time()
        timeout_s = timeout_s or self.default_timeout_s
        try:
            client: LLMClient = make_client(provider=self.provider, model=self.model)
        except Exception as e:
            return SolverResult(
                solver_name=self.name, exam_id=exam.instance_id, patch="",
                wall_clock_s=time.time() - started, errored=True, error_message=str(e),
            )

        # Snapshot pre-edit state for diff generation
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

        user = (
            f"Problem statement:\n\n{exam.problem_statement}\n\n"
            f"The repository is checked out in your tools' file namespace.\n"
            f"Failing tests: {', '.join(exam.FAIL_TO_PASS[:5])}\n\n"
            f"Fix the bug. When ready, call emit_patch."
        )

        handlers = self._tool_handlers(workdir)
        try:
            result = client.run_agent_loop(
                system=SYSTEM_PROMPT,
                user=user,
                tools=TOOLS,
                tool_handlers=handlers,
                terminal_tools=TERMINAL,
                max_turns=self.max_turns,
                max_tokens=4000,
            )
        except Exception as e:
            return SolverResult(
                solver_name=self.name, exam_id=exam.instance_id,
                patch=self._compute_diff(workdir),
                wall_clock_s=time.time() - started, errored=True, error_message=str(e),
            )

        patch = self._compute_diff(workdir)
        return SolverResult(
            solver_name=self.name,
            exam_id=exam.instance_id,
            patch=patch,
            wall_clock_s=time.time() - started,
            token_usage={"input": result.input_tokens, "output": result.output_tokens},
            errored=result.error is not None,
            error_message=result.error,
        )

    def _compute_diff(self, workdir: Path) -> str:
        try:
            res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(workdir), capture_output=True, text=True, timeout=60,
            )
            return res.stdout
        except Exception as e:
            log.warning("git diff failed: %r", e)
            return ""


# Alias under a more honest name
LLMDirectSolver = ClaudeDirectSolver
