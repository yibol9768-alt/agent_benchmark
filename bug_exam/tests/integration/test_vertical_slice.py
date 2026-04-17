"""End-to-end integration test against tests/fixtures/tiny_py_repo/.

Exercises (without Docker or any network calls):
  - RepoManifest + Database round-trip
  - validator/test_gates: all 8 gates against a real pytest run via local_runner
  - evaluator/scoring: F2P/P2P grading predicate
  - scoring/bradley_terry: BT fit + leaderboard JSON

This is the highest-value test we have. If it passes, the pipeline is
mechanically sound — the remaining risks are the Docker harness, GitHub
harvester, and the LLM injector/solver calls (all network-bound).

Strategy:
  1. Copy tiny_py_repo to a tmp dir and `git init` + commit HEAD.
  2. Baseline-run pytest, record the passing set.
  3. Build a BreakPlan + corresponding unified diff by hand
     (InvertedCondition on `is_sorted`).
  4. Run validate_injection with run_tests_fn=local_runner.run_with_patch.
  5. Assert the validator produces ExamStatus.VALIDATED with the expected
     F, S, FAIL_TO_PASS, PASS_TO_PASS sets.
  6. Simulate two solvers: one that correctly reverts the bug, one that
     leaves it in place. Grade both and fit BT.
  7. Assert the correct solver outranks the broken one.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from bug_exam.db import Database
from bug_exam.evaluator.local_runner import run_pytest, run_with_patch
from bug_exam.evaluator.scoring import grade_run
from bug_exam.schema import (
    BreakPlan,
    BreakStep,
    ExamInstance,
    ExamStatus,
    Language,
    MutationOp,
    RepoManifest,
    RepoStatus,
    make_instance_id,
)
from bug_exam.scoring.bradley_terry import build_pairwise_from_grades, fit as bt_fit
from bug_exam.scoring.leaderboard import build_leaderboard
from bug_exam.validator.dedup import patch_hash
from bug_exam.validator.test_gates import validate_injection


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_py_repo"
PKG_SRC = "src"


# -----------------------------------------------------------------------------
# Fixture helpers
# -----------------------------------------------------------------------------

def _init_git_repo(src_dir: Path, dest: Path) -> str:
    """Copy fixture to dest and make it a one-commit git repo. Return HEAD sha."""
    shutil.copytree(src_dir, dest)
    env = {
        "GIT_AUTHOR_NAME": "bug-exam-test",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "bug-exam-test",
        "GIT_COMMITTER_EMAIL": "t@e.x",
        "HOME": str(dest),  # isolate from user config
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(dest), check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=str(dest), check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(dest), check=True, env=env)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(dest),
                         capture_output=True, text=True, check=True, env=env)
    return res.stdout.strip()


def _run_tests_passing(repo: Path) -> set[str]:
    """Baseline run against a clean repo; returns the passing test id set."""
    result = run_pytest(repo, extra_pythonpath=str(repo / PKG_SRC))
    return set(result.passed_tests)


# -----------------------------------------------------------------------------
# The InvertedCondition bug: flip `xs[i] > xs[i + 1]` in list_ops.is_sorted
# so it says `<` instead. This breaks test_is_sorted_true on [1,2,3] (will
# report not-sorted) and test_is_sorted_false on [1,3,2] (will report sorted).
# test_is_sorted_empty stays green.
# -----------------------------------------------------------------------------

_INVERTED_IS_SORTED_PATCH = """\
diff --git a/src/tiny_pkg/list_ops.py b/src/tiny_pkg/list_ops.py
--- a/src/tiny_pkg/list_ops.py
+++ b/src/tiny_pkg/list_ops.py
@@ -15,6 +15,6 @@ def find_last(xs: list, needle) -> int:

 def is_sorted(xs: list[int]) -> bool:
     for i in range(len(xs) - 1):
-        if xs[i] > xs[i + 1]:
+        if xs[i] < xs[i + 1]:
             return False
     return True
"""

_REVERT_IS_SORTED_PATCH = """\
diff --git a/src/tiny_pkg/list_ops.py b/src/tiny_pkg/list_ops.py
--- a/src/tiny_pkg/list_ops.py
+++ b/src/tiny_pkg/list_ops.py
@@ -15,6 +15,6 @@ def find_last(xs: list, needle) -> int:

 def is_sorted(xs: list[int]) -> bool:
     for i in range(len(xs) - 1):
-        if xs[i] < xs[i + 1]:
+        if xs[i] > xs[i + 1]:
             return False
     return True
"""


def _build_break_plan() -> BreakPlan:
    return BreakPlan(
        target_F=1,
        target_S=1,
        steps=[
            BreakStep(
                op=MutationOp.InvertedCondition,
                file="src/tiny_pkg/list_ops.py",
                line=18,
                anchor_snippet="xs[i] > xs[i + 1]",
                rationale="Flip the strict comparison so is_sorted lies about ordering.",
            ),
        ],
        summary=(
            "Calls to is_sorted return the opposite of the correct answer for "
            "non-empty lists. Users report that tiny_pkg.is_sorted([1, 2, 3]) "
            "yields False and tiny_pkg.is_sorted([1, 3, 2]) yields True."
        ),
    )


# -----------------------------------------------------------------------------
# The test
# -----------------------------------------------------------------------------

@pytest.fixture
def work_repo() -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "work"
        head = _init_git_repo(FIXTURE, dest)
        yield dest, head  # type: ignore[misc]


def test_vertical_slice(work_repo, tmp_path) -> None:
    repo_dir, base_commit = work_repo

    # 1. Baseline must be stable and >= some tests
    baseline_passing = _run_tests_passing(repo_dir)
    assert len(baseline_passing) >= 16, (
        f"expected ≥ 16 passing tests in the fixture, got {len(baseline_passing)}"
    )
    # Re-run once to confirm determinism on this fixture
    assert _run_tests_passing(repo_dir) == baseline_passing

    # 2. Set up a tiny sqlite DB and insert a RepoManifest for the fixture
    db = Database(tmp_path / "status.db")
    repo_id = "fixture__tiny_py_repo"
    now = datetime(2025, 10, 1)
    manifest = RepoManifest(
        id=repo_id,
        url="file://" + str(FIXTURE),
        owner="fixture", name="tiny_py_repo",
        language=Language.PYTHON,
        stars=0, size_kb=1,
        license="MIT",
        created_at=now, pushed_at=now,
        base_commit=base_commit,
        default_branch="main",
        status=RepoStatus.BASELINE_OK,
        post_cutoff=True,
        test_framework="pytest",
        baseline_test_count=len(baseline_passing),
    )
    db.upsert_repo(manifest)

    # 3. Build the break plan + candidate diff
    plan = _build_break_plan()
    diff = _INVERTED_IS_SORTED_PATCH

    # 4. Run validator with local runner as the test backend
    def run_tests_fn(candidate_diff: str) -> tuple[list[str], list[str]]:
        res = run_with_patch(
            repo_dir,
            candidate_diff,
            base_commit=base_commit,
            extra_pythonpath=str(repo_dir / PKG_SRC),
        )
        return res.passed_tests, res.failed_tests

    report = validate_injection(
        repo_dir=repo_dir,
        base_commit=base_commit,
        plan=plan,
        diff=diff,
        db=db,
        image_tag="not-used-in-local-mode",
        run_tests_fn=run_tests_fn,
        run_baseline_passing=baseline_passing,
    )
    assert report.ok, (
        f"validator failed at gate {report.gate_failed}: {report.failure_reason}\n"
        f"gates passed: {report.gates_passed}"
    )
    assert report.validated_steps == 1
    assert report.unique_files == 1
    # Flipping > to < in is_sorted: only test_is_sorted_true (which feeds
    # [1,2,3]) breaks. test_is_sorted_false passes [1,3,2] which still
    # returns False because the first pair 1<3 already trips the flipped
    # early return. test_is_sorted_empty stays green because the loop
    # never executes.
    assert any("test_is_sorted_true" in t for t in report.fail_to_pass), (
        f"expected is_sorted_true in F2P, got {report.fail_to_pass}"
    )
    assert not any("test_is_sorted_empty" in t for t in report.fail_to_pass)
    assert any("test_is_sorted_false" in t for t in report.pass_to_pass)
    assert any("test_is_sorted_empty" in t for t in report.pass_to_pass)
    assert any("test_running_sum_basic" in t for t in report.pass_to_pass)

    # 5. Insert the resulting exam row
    ph = patch_hash(diff)
    exam = ExamInstance(
        instance_id=make_instance_id(repo_id, "trivial", ph),
        repo_id=repo_id, repo_url=manifest.url, language=Language.PYTHON,
        base_commit=base_commit,
        injection_patch=diff,
        break_plan=plan,
        injector_model="fixture",
        patch_hash=ph,
        difficulty_band="trivial",
        F=report.unique_files, S=report.validated_steps,
        FAIL_TO_PASS=report.fail_to_pass,
        PASS_TO_PASS=report.pass_to_pass,
        selected_test_files=[],
        problem_statement=plan.summary,
        base_dockerfile_path="", instance_dockerfile_path="",
        run_script_path="", parser_path="",
        test_framework="pytest",
        post_cutoff=True,
        status=ExamStatus.FROZEN,
    )
    db.upsert_exam(exam)
    db.set_exam_status(exam.instance_id, ExamStatus.FROZEN)

    # 6. Simulate two solvers:
    #    (a) smart_solver: emits the correct revert patch
    #    (b) broken_solver: emits an empty patch (does nothing)
    for solver_name, solver_patch in [
        ("smart_solver", _REVERT_IS_SORTED_PATCH),
        ("broken_solver", ""),
    ]:
        # Apply bug first, then solver patch on top, then run tests
        run_tests_dir = repo_dir
        # Stack both patches by applying bug + solver together
        def combined_run(s_patch: str) -> tuple[list[str], list[str]]:
            # We need to reset, apply bug, apply solver, run tests, reset
            from bug_exam.evaluator.local_runner import (
                apply_patch, reset_checkout, run_pytest as _rp,
            )
            reset_checkout(run_tests_dir, base_commit)
            ok, stderr = apply_patch(run_tests_dir, diff)
            if not ok:
                reset_checkout(run_tests_dir, base_commit)
                return [], []
            if s_patch.strip():
                ok2, _ = apply_patch(run_tests_dir, s_patch)
                if not ok2:
                    reset_checkout(run_tests_dir, base_commit)
                    return [], []
            try:
                res = _rp(run_tests_dir, extra_pythonpath=str(run_tests_dir / PKG_SRC))
                return res.passed_tests, res.failed_tests
            finally:
                reset_checkout(run_tests_dir, base_commit)

        passed, failed = combined_run(solver_patch)
        grade = grade_run(
            exam=exam, passed_tests=passed, failed_tests=failed,
            run_id=f"{solver_name}__{uuid.uuid4().hex[:8]}",
            solver_name=solver_name,
        )
        db.upsert_run(grade.run_id, exam.instance_id, solver_name,
                      result=None, status=__import__("bug_exam.schema", fromlist=["RunStatus"]).RunStatus.COMPLETED)
        db.upsert_grade(grade)

    grades = db.list_grades()
    by_solver = {g.solver_name: g for g in grades}
    assert by_solver["smart_solver"].final_passed is True, (
        f"smart solver should pass; got f2p={by_solver['smart_solver'].f2p_pass} "
        f"p2p={by_solver['smart_solver'].p2p_pass}"
    )
    assert by_solver["broken_solver"].final_passed is False

    # 7. BT fit — smart_solver should outrank broken_solver
    payload = build_leaderboard(db)
    solvers = {e["solver_name"]: e for e in payload["solvers"]}
    assert "smart_solver" in solvers
    assert "broken_solver" in solvers
    assert solvers["smart_solver"]["bt_rating"] > solvers["broken_solver"]["bt_rating"]
    assert solvers["smart_solver"]["pass_rate_overall"] == 1.0
    assert solvers["broken_solver"]["pass_rate_overall"] == 0.0
    assert payload["n_runs"] == 2
    assert payload["n_exams"] == 1
