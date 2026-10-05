from __future__ import annotations

import random
from typing import Protocol, Sequence


class RandomSource(Protocol):
    def randint(self, low: int, high: int) -> int:
        """Return an integer in the inclusive range."""


class SequenceSource:
    """Fixed rolls for tests and deterministic replays. Not the global RNG."""

    def __init__(self, values: Sequence[int]) -> None:
        self._values = list(values)
        self.consumed = 0

    def randint(self, low: int, high: int) -> int:
        if self.consumed >= len(self._values):
            raise RuntimeError("RandomSource exhausted")
        value = self._values[self.consumed]
        if value < low or value > high:
            raise RuntimeError("RandomSource value out of range")
        self.consumed += 1
        return value


class LocalRandomSource:
    """Owns a random.Random instance. Does not call the global random functions."""

    def __init__(self, generator: random.Random | None = None) -> None:
        self._generator = random.Random() if generator is None else generator

    def randint(self, low: int, high: int) -> int:
        return self._generator.randint(low, high)


class Dice:
    def __init__(self, source: RandomSource) -> None:
        self._source = source

    def roll(self, sides: int = 20) -> int:
        if sides < 2:
            raise ValueError("A die needs at least 2 sides")
        return self._source.randint(1, sides)
