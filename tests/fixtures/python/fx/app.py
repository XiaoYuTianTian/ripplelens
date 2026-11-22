from collections.abc import Callable

from fx.lib import Circle, helper, total


def a() -> int:
    return helper(1)


def b() -> int:
    return helper(2) + a()


def register(f: Callable[[int], int]) -> int:
    return f(3)


def c() -> int:
    return register(helper)


def d() -> float:
    return Circle(1).area()


def e() -> float:
    return total([Circle(2)])
