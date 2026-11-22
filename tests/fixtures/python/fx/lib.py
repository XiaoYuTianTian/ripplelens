from abc import ABC, abstractmethod

LIMIT = 10


class Shape(ABC):
    @abstractmethod
    def area(self) -> float: ...


class Circle(Shape):
    def __init__(self, r: float) -> None:
        self.r = r

    def area(self) -> float:
        return 3.14 * self.r * self.r


def helper(x: int) -> int:
    return x + LIMIT


def total(shapes: list[Shape]) -> float:
    t = 0.0
    for s in shapes:
        t += s.area()
    return t
