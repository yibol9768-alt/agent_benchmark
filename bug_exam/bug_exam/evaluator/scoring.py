"""The scoring predicate.

Direct adaptation of swe_bench_pro_eval.py:554-559 — kept minimal and
side-effect-free so it can be unit-tested without Docker.
"""
from __future__ import annotations

from ..schema import ExamInstance, Grade


def grade_run(
    exam: ExamInstance,
    passed_tests: list[str],
    failed_tests: list[str],
    run_id: str,
    solver_name: str,
    stderr_excerpt: str = "",
) -> Grade:
    """Compute a Grade for one solver run.

    Semantics (identical to SWE-bench Pro):
        passed_tests is the set parsed from the test runner output.
        f2p_pass  = all FAIL_TO_PASS tests are in passed_tests
        p2p_pass  = all PASS_TO_PASS tests are in passed_tests
        final_passed = f2p_pass AND p2p_pass
    """
    passed_set = set(passed_tests)
    f2p = set(exam.FAIL_TO_PASS)
    p2p = set(exam.PASS_TO_PASS)

    f2p_pass = f2p.issubset(passed_set)
    p2p_pass = p2p.issubset(passed_set)
    final_passed = f2p_pass and p2p_pass

    return Grade(
        run_id=run_id,
        exam_id=exam.instance_id,
        solver_name=solver_name,
        passed_tests=sorted(passed_set),
        failed_tests=sorted(set(failed_tests)),
        f2p_pass=f2p_pass,
        p2p_pass=p2p_pass,
        final_passed=final_passed,
        stderr_excerpt=stderr_excerpt[-2000:],
    )
