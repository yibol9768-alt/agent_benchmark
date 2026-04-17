"""The eight validation gates.

Given a candidate injection (plan + diff), run the gates in order and short-
circuit on the first failure. Returns a ValidationReport with the outcome
and enough metadata to record on the Exam row.

Gates:
  G1. Patch applies cleanly against HEAD
  G2. Post-patch source parses (all modified Python files compile)
  G3. Declared F <= unique files in diff
  G4. Declared S <= operator_check-validated steps
  G5. Patch hash is new (not in DB)
  G6. Post-patch test run produces a FAIL_TO_PASS set of size 1..10
  G7. Baseline P2P set (all tests NOT in F2P) stays green
  G8. Solvability oracle confirms the bug is fixable (Phase 2 gate; stubbed)
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..evaluator.docker_runner import run_exam_in_docker
from ..evaluator.scoring import grade_run
from ..schema import BreakPlan, BreakStep, ExamInstance, ExamStatus, MutationOp
from .ast_diff import files_touched
from .dedup import is_duplicate, patch_hash
from .operator_check import check_python

log = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    ok: bool
    gates_passed: list[str] = field(default_factory=list)
    gate_failed: str | None = None
    failure_reason: str | None = None
    validated_steps: int = 0
    unique_files: int = 0
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)


def _read_file_at_commit(repo_dir: Path, commit: str, rel_path: str) -> str:
    try:
        res = subprocess.run(
            ["git", "show", f"{commit}:{rel_path}"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=30,
        )
        if res.returncode == 0:
            return res.stdout
    except Exception:
        pass
    return ""


def _read_file_plain(repo_dir: Path, rel_path: str) -> str:
    p = repo_dir / rel_path
    return p.read_text(errors="replace") if p.exists() else ""


def validate_injection(
    *,
    repo_dir: Path,
    base_commit: str,
    plan: BreakPlan,
    diff: str,
    db,
    image_tag: str,
    run_tests_fn: Callable[[str], tuple[list[str], list[str]]] | None = None,
    run_baseline_passing: set[str] | None = None,
    apply_patch_in_workdir: bool = True,
) -> ValidationReport:
    """Run the eight gates against a candidate injection.

    Parameters
    ----------
    repo_dir
        A checkout of the target repo at `base_commit`. The validator will
        reset this checkout to HEAD and optionally apply the candidate diff
        so that operator_check can parse the post-patch source.
    run_tests_fn
        A callable that applies the diff inside the image and returns
        (passing, failing). If None, this gate is skipped. The orchestrator
        wires this to evaluator.docker_runner.
    run_baseline_passing
        The stable baseline passing set (from envbuild). Required for G6/G7.
    """
    report = ValidationReport(ok=False)

    # G1: patch applies cleanly. LLMs routinely get hunk-header line counts
    # wrong; we use --recount so git recomputes counts from the actual body.
    patch_path = repo_dir / ".bug_exam_candidate.diff"
    patch_path.write_text(diff)
    try:
        res = subprocess.run(
            ["git", "apply", "--check", "--recount", "--whitespace=nowarn", str(patch_path.name)],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
        if res.returncode != 0:
            report.gate_failed = "G1"
            report.failure_reason = f"git apply --check failed: {res.stderr[-500:]}"
            return report
    finally:
        try:
            patch_path.unlink()
        except Exception:
            pass
    report.gates_passed.append("G1")

    # Count unique files in diff for G3
    unique_files = files_touched(diff)
    report.unique_files = len(unique_files)

    # G3: declared F <= actual
    if plan.target_F > len(unique_files):
        report.gate_failed = "G3"
        report.failure_reason = f"declared F={plan.target_F} > diff files={len(unique_files)}"
        return report
    report.gates_passed.append("G3")

    # Apply the diff into the workdir for G2 + G4
    if apply_patch_in_workdir:
        patch_path = repo_dir / ".bug_exam_apply.diff"
        patch_path.write_text(diff)
        try:
            res = subprocess.run(
                ["git", "apply", "--recount", "--whitespace=nowarn", str(patch_path.name)],
                cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
            )
            if res.returncode != 0:
                report.gate_failed = "G1"
                report.failure_reason = f"git apply failed at application stage: {res.stderr[-500:]}"
                return report
        finally:
            try:
                patch_path.unlink()
            except Exception:
                pass

    # G2 + G4: per-step AST check
    validated = 0
    for step in plan.steps:
        src_before = _read_file_at_commit(repo_dir, base_commit, step.file)
        src_after = _read_file_plain(repo_dir, step.file)
        if not src_before or not src_after:
            continue
        result = check_python(step, src_before, src_after)
        if result.ok:
            validated += 1
    report.validated_steps = validated
    # G2 (parse): check_python already returns ok=False if post parse fails.
    # That doesn't fail G2 explicitly unless ALL steps fail due to parse. We
    # explicitly parse modified Python files here for a sharper G2.
    import ast as _ast
    for f in unique_files:
        if not f.endswith(".py"):
            continue
        src = _read_file_plain(repo_dir, f)
        try:
            _ast.parse(src)
        except SyntaxError as e:
            report.gate_failed = "G2"
            report.failure_reason = f"post-patch {f} fails to parse: {e}"
            return report
    report.gates_passed.append("G2")

    if plan.target_S > validated:
        report.gate_failed = "G4"
        report.failure_reason = f"declared S={plan.target_S} > validated steps={validated}"
        return report
    report.gates_passed.append("G4")

    # G5: dedup
    if is_duplicate(db, diff):
        report.gate_failed = "G5"
        report.failure_reason = "duplicate patch hash"
        return report
    report.gates_passed.append("G5")

    # G6/G7: test execution
    if run_tests_fn is not None and run_baseline_passing is not None:
        passing, failing = run_tests_fn(diff)
        newly_failing = run_baseline_passing - set(passing)
        still_passing = set(passing) & run_baseline_passing
        if not (1 <= len(newly_failing) <= 10):
            report.gate_failed = "G6"
            report.failure_reason = f"|FAIL_TO_PASS|={len(newly_failing)} out of range [1,10]"
            return report
        report.gates_passed.append("G6")
        p2p = run_baseline_passing - newly_failing
        if not p2p.issubset(set(passing)):
            missing = p2p - set(passing)
            report.gate_failed = "G7"
            report.failure_reason = f"PASS_TO_PASS broke {len(missing)} tests, sample: {sorted(list(missing))[:5]}"
            return report
        report.gates_passed.append("G7")
        report.fail_to_pass = sorted(newly_failing)
        report.pass_to_pass = sorted(p2p)
    else:
        log.info("skipping G6/G7 (no test runner wired)")

    # G8: solvability oracle — stubbed in Phase 1, always pass
    report.gates_passed.append("G8")
    report.ok = True
    return report
