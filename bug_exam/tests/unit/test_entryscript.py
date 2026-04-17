"""Unit test: entryscript generation shape."""
from datetime import datetime
from bug_exam.evaluator.entryscript import build_entryscript
from bug_exam.schema import (
    BreakPlan, BreakStep, ExamInstance, Language, MutationOp,
)


def _ex() -> ExamInstance:
    return ExamInstance(
        instance_id="ex1", repo_id="r", repo_url="u", language=Language.PYTHON,
        base_commit="deadbeef",
        injection_patch="diff --git a/f.py b/f.py\n",
        break_plan=BreakPlan(target_F=1, target_S=1, steps=[
            BreakStep(op=MutationOp.OffByOne, file="f.py", line=1, anchor_snippet="", rationale="")
        ], summary=""),
        injector_model="x", patch_hash="h", difficulty_band="trivial",
        F=1, S=1, FAIL_TO_PASS=[], PASS_TO_PASS=[],
        selected_test_files=["tests/test_foo.py", "tests/test_bar.py"],
        problem_statement="",
        base_dockerfile_path="", instance_dockerfile_path="",
        run_script_path="", parser_path="", test_framework="pytest",
        before_repo_set_cmd="pip install -e .",
    )


def test_entryscript_solver():
    exam = _ex()
    script = build_entryscript(exam, base_dockerfile_text="ENV FOO=bar", instance_dockerfile_text="")
    assert "export FOO=bar" in script
    assert "git apply -v /workspace/bug_patch.diff" in script
    assert "git apply -v /workspace/patch.diff" in script
    assert "deadbeef" in script
    assert "tests/test_foo.py,tests/test_bar.py" in script


def test_entryscript_bug_only():
    exam = _ex()
    script = build_entryscript(exam, "", "", patch_kind="bug_only")
    assert "git apply -v /workspace/bug_patch.diff" in script
    assert "git apply -v /workspace/patch.diff" not in script


def test_entryscript_baseline():
    exam = _ex()
    script = build_entryscript(exam, "", "", patch_kind="baseline")
    assert "no patch" in script
