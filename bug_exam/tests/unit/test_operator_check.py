"""Unit tests for the Python AST operator validator."""
from bug_exam.schema import BreakStep, MutationOp
from bug_exam.validator.operator_check import check_python


def test_off_by_one_range():
    before = "def f(n):\n    for i in range(n):\n        yield i\n"
    after  = "def f(n):\n    for i in range(n - 1):\n        yield i\n"
    step = BreakStep(op=MutationOp.OffByOne, file="m.py", line=2, anchor_snippet="range", rationale="")
    r = check_python(step, before, after)
    assert r.ok


def test_inverted_condition():
    before = "def f(x):\n    if x > 0:\n        return 1\n    return 0\n"
    after  = "def f(x):\n    if x < 0:\n        return 1\n    return 0\n"
    step = BreakStep(op=MutationOp.InvertedCondition, file="m.py", line=2, anchor_snippet="x > 0", rationale="")
    assert check_python(step, before, after).ok


def test_wrong_binary_op():
    before = "def f(a, b):\n    return a + b\n"
    after  = "def f(a, b):\n    return a - b\n"
    step = BreakStep(op=MutationOp.WrongBinaryOperator, file="m.py", line=2, anchor_snippet="a + b", rationale="")
    assert check_python(step, before, after).ok


def test_swapped_args():
    before = "def f(a, b):\n    return g(a, b)\n"
    after  = "def f(a, b):\n    return g(b, a)\n"
    step = BreakStep(op=MutationOp.SwappedArgs, file="m.py", line=2, anchor_snippet="g(", rationale="")
    assert check_python(step, before, after).ok


def test_structural_break_rejected():
    before = "def f():\n    return 1\n"
    after  = "def f():\n    retur 1\n"   # syntax error
    step = BreakStep(op=MutationOp.OffByOne, file="m.py", line=2, anchor_snippet="return", rationale="")
    r = check_python(step, before, after)
    assert not r.ok
    assert "parse" in r.reason
