"""
Modulation index history tracker — ring buffer with least-squares trend.
"""

from __future__ import annotations

import numpy as np

from .types import SidebandResult


class ModulationHistory:
    """Ring buffer of (timestamp, modulation_index) pairs with least-squares growth-rate estimation."""

    def __init__(self, capacity: int = 30):
        self._capacity = capacity
        self._times = np.zeros(capacity, dtype=np.float64)
        self._mi_values = np.zeros(capacity, dtype=np.float64)
        self._count = 0
        self._head = 0

    def update(self, result: SidebandResult) -> None:
        self._times[self._head] = result.timestamp
        self._mi_values[self._head] = result.modulation_index
        self._head = (self._head + 1) % self._capacity
        self._count += 1

    @property
    def filled(self) -> int:
        return min(self._count, self._capacity)

    # Need at least 3 points for a meaningful linear fit
    @property
    def has_enough_history(self) -> bool:
        return self.filled >= 3

    # Reconstruct chronological order after ring-buffer wrap-around
    def _ordered_slice(self):
        n = self.filled
        if self._count <= self._capacity:
            return self._times[:n], self._mi_values[:n]
        idx = np.concatenate(
            [
                np.arange(self._head, self._capacity),
                np.arange(0, self._head),
            ]
        )
        return self._times[idx], self._mi_values[idx]

    # dMI/dt via linear least-squares: MI = slope * t + intercept
    def growth_rate(self) -> float:
        if not self.has_enough_history:
            return 0.0

        times, mi = self._ordered_slice()
        t_norm = times - times[0]
        A = np.column_stack([t_norm, np.ones_like(t_norm)])
        result, _, _, _ = np.linalg.lstsq(A, mi, rcond=None)
        return float(result[0])

    def is_growing(self, threshold: float = 0.001) -> bool:
        return self.growth_rate() > threshold

    def current_mi(self) -> float:
        if self._count == 0:
            return 0.0
        last = (self._head - 1) % self._capacity
        return float(self._mi_values[last])

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        return self._ordered_slice()
