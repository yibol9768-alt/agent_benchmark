"""Unit tests for BT + Elo scoring on synthetic outcomes."""
from bug_exam.scoring.bradley_terry import build_pairwise_from_grades, fit
from bug_exam.scoring.elo import EloState, batch_update
from bug_exam.evaluator.scoring import grade_run
from bug_exam.schema import (
    BreakPlan, BreakStep, ExamInstance, Language, MutationOp,
)
from datetime import datetime


def _fake_exam(f2p: list[str], p2p: list[str]) -> ExamInstance:
    return ExamInstance(
        instance_id="ex1", repo_id="r", repo_url="u", language=Language.PYTHON,
        base_commit="c", injection_patch="",
        break_plan=BreakPlan(target_F=1, target_S=1, steps=[
            BreakStep(op=MutationOp.OffByOne, file="a.py", line=1, anchor_snippet="", rationale="")
        ], summary=""),
        injector_model="x", patch_hash="h", difficulty_band="trivial",
        F=1, S=1, FAIL_TO_PASS=f2p, PASS_TO_PASS=p2p, selected_test_files=[],
        problem_statement="", base_dockerfile_path="", instance_dockerfile_path="",
        run_script_path="", parser_path="", test_framework="pytest",
    )


def test_grade_run_final_passed_true():
    exam = _fake_exam(["t::a"], ["t::b"])
    g = grade_run(exam, passed_tests=["t::a", "t::b", "t::c"], failed_tests=[], run_id="r", solver_name="s")
    assert g.f2p_pass and g.p2p_pass and g.final_passed


def test_grade_run_f2p_missing():
    exam = _fake_exam(["t::a"], ["t::b"])
    g = grade_run(exam, passed_tests=["t::b"], failed_tests=["t::a"], run_id="r", solver_name="s")
    assert not g.f2p_pass and g.p2p_pass and not g.final_passed


def test_bt_orders_solvers():
    # A dominates B on every exam, B dominates C on every exam
    grades = []
    for k in range(20):
        grades.append({"exam_id": f"e{k}", "solver_name": "A", "final_passed": True})
        grades.append({"exam_id": f"e{k}", "solver_name": "B", "final_passed": False})
        grades.append({"exam_id": f"e{k}", "solver_name": "C", "final_passed": False})
    pairs = build_pairwise_from_grades(grades)
    result = fit(["A", "B", "C"], pairs, bootstrap=30)
    assert result.ratings["A"] > result.ratings["B"]
    assert result.ratings["B"] >= result.ratings["C"]


def test_elo_basic():
    state = EloState()
    state.update_pair("A", "B", 1.0)
    assert state.get("A") > 1500
    assert state.get("B") < 1500
