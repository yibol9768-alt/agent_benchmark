from tiny_pkg import add, sub, clamp, running_sum


def test_add():
    assert add(1, 2) == 3
    assert add(-5, 5) == 0


def test_sub():
    assert sub(5, 3) == 2
    assert sub(0, 7) == -7


def test_clamp_below():
    assert clamp(-1, 0, 10) == 0


def test_clamp_above():
    assert clamp(11, 0, 10) == 10


def test_clamp_in_range():
    assert clamp(5, 0, 10) == 5


def test_running_sum_empty():
    assert running_sum([]) == []


def test_running_sum_basic():
    assert running_sum([1, 2, 3]) == [1, 3, 6]


def test_running_sum_negatives():
    assert running_sum([1, -1, 1]) == [1, 0, 1]
