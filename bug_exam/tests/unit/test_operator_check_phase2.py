"""Unit tests for the 11 Phase-2 Python mutation operators."""
from bug_exam.schema import BreakStep, MutationOp
from bug_exam.validator.operator_check import check_python


def _step(op: MutationOp, line: int) -> BreakStep:
    return BreakStep(op=op, file="m.py", line=line, anchor_snippet="", rationale="")


def test_removed_guard_if():
    before = "def f(x):\n    if x is None:\n        return 0\n    return x * 2\n"
    after  = "def f(x):\n    return x * 2\n"
    assert check_python(_step(MutationOp.RemovedGuard, 2), before, after).ok


def test_removed_guard_assert():
    before = "def f(x):\n    assert x >= 0\n    return x\n"
    after  = "def f(x):\n    return x\n"
    assert check_python(_step(MutationOp.RemovedGuard, 2), before, after).ok


def test_dropped_return_value():
    before = "def f():\n    return 42\n"
    after  = "def f():\n    return None\n"
    assert check_python(_step(MutationOp.DroppedReturn, 2), before, after).ok


def test_dropped_return_deleted():
    before = "def f(x):\n    return x + 1\n"
    after  = "def f(x):\n    x + 1\n"
    assert check_python(_step(MutationOp.DroppedReturn, 2), before, after).ok


def test_switched_constant_string():
    before = "def greeting():\n    return 'hello'\n"
    after  = "def greeting():\n    return 'goodbye'\n"
    assert check_python(_step(MutationOp.SwitchedConstant, 2), before, after).ok


def test_switched_constant_excludes_off_by_one():
    # A pure ±1 change should be rejected — that's OffByOne territory
    before = "x = 5\n"
    after  = "x = 6\n"
    r = check_python(_step(MutationOp.SwitchedConstant, 1), before, after)
    assert not r.ok


def test_flipped_boolean():
    before = "DEBUG = True\n"
    after  = "DEBUG = False\n"
    assert check_python(_step(MutationOp.FlippedBoolean, 1), before, after).ok


def test_wrong_exception_type_raise():
    before = "def f():\n    raise ValueError('bad')\n"
    after  = "def f():\n    raise KeyError('bad')\n"
    assert check_python(_step(MutationOp.WrongExceptionType, 2), before, after).ok


def test_wrong_exception_type_except():
    before = "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n"
    after  = "def f():\n    try:\n        pass\n    except KeyError:\n        pass\n"
    assert check_python(_step(MutationOp.WrongExceptionType, 4), before, after).ok


def test_missing_await():
    before = "async def f():\n    x = await g()\n    return x\n"
    after  = "async def f():\n    x = g()\n    return x\n"
    assert check_python(_step(MutationOp.MissingAwait, 2), before, after).ok


def test_wrong_loop_bound_for():
    before = "def f(n):\n    for i in range(n):\n        yield i\n"
    after  = "def f(n):\n    for i in range(n // 2):\n        yield i\n"
    assert check_python(_step(MutationOp.WrongLoopBound, 2), before, after).ok


def test_wrong_loop_bound_while():
    before = "def f(n):\n    while n > 0:\n        n -= 1\n"
    after  = "def f(n):\n    while n > 1:\n        n -= 1\n"
    assert check_python(_step(MutationOp.WrongLoopBound, 2), before, after).ok


def test_state_reorder():
    before = "def f():\n    a = 1\n    b = a + 1\n    c = b + 1\n    return c\n"
    after  = "def f():\n    b = a + 1\n    a = 1\n    c = b + 1\n    return c\n"
    assert check_python(_step(MutationOp.StateReorder, 3), before, after).ok


def test_shadowed_variable():
    before = "def f():\n    x = 1\n    return x\n"
    after  = "def f():\n    y = 1\n    return x\n"
    # Intentionally a broken rename — checker only verifies the Store changed
    assert check_python(_step(MutationOp.ShadowedVariable, 2), before, after).ok


def test_incorrect_type_cast_removed():
    before = "def f(x):\n    return int(x) + 1\n"
    after  = "def f(x):\n    return x + 1\n"
    assert check_python(_step(MutationOp.IncorrectTypeCast, 2), before, after).ok


def test_incorrect_type_cast_inserted():
    before = "def f(x):\n    return x + 1\n"
    after  = "def f(x):\n    return str(x) + 1\n"
    assert check_python(_step(MutationOp.IncorrectTypeCast, 2), before, after).ok


def test_omitted_side_effect():
    before = "def f(logger, msg):\n    logger.info(msg)\n    return msg\n"
    after  = "def f(logger, msg):\n    return msg\n"
    assert check_python(_step(MutationOp.OmittedSideEffect, 2), before, after).ok


# Negative tests — the validator should fail closed on unrelated changes

def test_off_by_one_does_not_satisfy_switched_constant():
    before = "x = 10\n"
    after  = "x = 11\n"
    assert not check_python(_step(MutationOp.SwitchedConstant, 1), before, after).ok


def test_unrelated_change_rejected():
    before = "def f():\n    return 1\n"
    after  = "def f():\n    return 1\n"
    # No change at all
    assert not check_python(_step(MutationOp.InvertedCondition, 2), before, after).ok
