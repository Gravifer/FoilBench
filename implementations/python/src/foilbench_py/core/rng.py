"""Cross-language PCG32 random generator."""

from collections.abc import Sequence

import numpy as np
from jaxtyping import Float32


class PCG32:
    """Small deterministic PCG-XSH-RR implementation.

    The arithmetic and float conversion are specified explicitly so Julia,
    TypeScript, and Rust implementations can reproduce the stream.
    """

    _MASK_64 = (1 << 64) - 1
    _MULTIPLIER = 6364136223846793005

    def __init__(self, seed: int, stream: int = 54) -> None:
        self._state = 0
        self._increment = ((stream << 1) | 1) & self._MASK_64
        self.next_uint32()
        self._state = (self._state + seed) & self._MASK_64
        self.next_uint32()

    def next_uint32(self) -> int:
        old = self._state
        self._state = (old * self._MULTIPLIER + self._increment) & self._MASK_64
        xorshifted = (((old >> 18) ^ old) >> 27) & 0xFFFFFFFF
        rotation = (old >> 59) & 31
        return ((xorshifted >> rotation) | (xorshifted << ((-rotation) & 31))) & 0xFFFFFFFF

    def random(self, shape: Sequence[int]) -> Float32[np.ndarray, "..."]:
        count = int(np.prod(shape, dtype=np.int64))
        values = np.empty(count, dtype=np.float32)
        scale = np.float32(1.0 / 4294967296.0)
        for index in range(count):
            values[index] = np.float32(self.next_uint32()) * scale
        return values.reshape(tuple(shape))

    def uniform(self, low: float, high: float, shape: Sequence[int]) -> Float32[np.ndarray, "..."]:
        return np.asarray(low + (high - low) * self.random(shape), dtype=np.float32)
