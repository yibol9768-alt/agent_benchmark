from tiny_pkg import reverse_in_place, find_last, is_sorted


def test_reverse_even():
    xs = [1, 2, 3, 4]
    reverse_in_place(xs)
    assert xs == [4, 3, 2, 1]


def test_reverse_odd():
    xs = [1, 2, 3, 4, 5]
    reverse_in_place(xs)
    assert xs == [5, 4, 3, 2, 1]


def test_find_last_hit():
    assert find_last([1, 2, 3, 2, 1], 2) == 3


def test_find_last_miss():
    assert find_last([1, 2, 3], 9) == -1


def test_find_last_single():
    assert find_last([7], 7) == 0


def test_is_sorted_true():
    assert is_sorted([1, 2, 3])


def test_is_sorted_false():
    assert not is_sorted([1, 3, 2])


def test_is_sorted_empty():
    assert is_sorted([])
