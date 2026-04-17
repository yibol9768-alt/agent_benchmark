def reverse_in_place(xs: list) -> None:
    n = len(xs)
    for i in range(n // 2):
        xs[i], xs[n - 1 - i] = xs[n - 1 - i], xs[i]


def find_last(xs: list, needle) -> int:
    """Return the index of the last occurrence of needle, or -1 if not found."""
    for i in range(len(xs) - 1, -1, -1):
        if xs[i] == needle:
            return i
    return -1


def is_sorted(xs: list[int]) -> bool:
    for i in range(len(xs) - 1):
        if xs[i] > xs[i + 1]:
            return False
    return True
