def add(a: int, b: int) -> int:
    return a + b


def sub(a: int, b: int) -> int:
    return a - b


def clamp(x: int, lo: int, hi: int) -> int:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def running_sum(xs: list[int]) -> list[int]:
    out: list[int] = []
    total = 0
    for v in xs:
        total += v
        out.append(total)
    return out
