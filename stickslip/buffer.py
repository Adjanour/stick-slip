"""
O(1) numpy ring buffer — push is a circular increment, no copies.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .types import Signal


class RingBuffer:
    """Fixed-capacity ring buffer backed by a pre-allocated numpy array.

    push() is O(1) — it copies the incoming samples into the ring at the
    write cursor and increments.  to_signal() returns a view (no copy) of the
    contiguous windowed data once full.
    """

    def __init__(self, capacity: int, sample_rate: float, channel: str):
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._buf = np.empty(capacity, dtype=np.float64)
        self._capacity = capacity
        self._sample_rate = sample_rate
        self._channel = channel
        self._write_pos = 0
        self._filled = 0
        self._last_timestamp = 0.0

    @property
    def is_full(self) -> bool:
        return self._filled >= self._capacity

    @property
    def fill_fraction(self) -> float:
        return min(self._filled / self._capacity, 1.0)

    @property
    def n_samples(self) -> int:
        return min(self._filled, self._capacity)

    def push(self, new_samples: np.ndarray, timestamp: float) -> "RingBuffer":
        """Copy new_samples into the ring at the write cursor.  Returns self."""
        n = len(new_samples)
        if n == 0:
            return self
        if n > self._capacity:
            new_samples = new_samples[-self._capacity :]
            n = self._capacity
        space = self._capacity - self._write_pos
        if n <= space:
            self._buf[self._write_pos : self._write_pos + n] = new_samples
        else:
            first = space
            self._buf[self._write_pos:] = new_samples[:first]
            self._buf[: n - first] = new_samples[first:]
        self._write_pos = (self._write_pos + n) % self._capacity
        self._filled += n
        self._last_timestamp = timestamp
        return self

    def to_signal(self) -> Optional[Signal]:
        if not self.is_full:
            return None
        # Contiguous view: the ring wraps so we may need two slices
        if self._filled <= self._capacity:
            samples = self._buf[: self._filled]
        else:
            samples = np.concatenate(
                [self._buf[self._write_pos :], self._buf[: self._write_pos]]
            )
        return Signal(
            samples=samples,
            sample_rate=self._sample_rate,
            timestamp=self._last_timestamp,
            channel=self._channel,
        )


def make_buffer(
    window_seconds: float, sample_rate: float, channel: str
) -> RingBuffer:
    return RingBuffer(
        capacity=int(window_seconds * sample_rate),
        sample_rate=sample_rate,
        channel=channel,
    )
