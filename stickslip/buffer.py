"""
Immutable rolling window buffer — push() returns a NEW buffer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .types import Signal


@dataclass(frozen=True)
class RollingBuffer:
    """Immutable rolling sample window for one channel."""

    data: np.ndarray
    max_size: int
    sample_rate: float
    channel: str
    last_timestamp: float = 0.0

    @property
    def is_full(self) -> bool:
        return len(self.data) >= self.max_size

    @property
    def fill_fraction(self) -> float:
        return len(self.data) / self.max_size

    @property
    def n_samples(self) -> int:
        return len(self.data)

    def push(self, new_samples: np.ndarray, timestamp: float) -> "RollingBuffer":
        combined = np.concatenate([self.data, new_samples])
        trimmed = combined[-self.max_size :].copy()
        return RollingBuffer(
            data=trimmed,
            max_size=self.max_size,
            sample_rate=self.sample_rate,
            channel=self.channel,
            last_timestamp=timestamp,
        )

    def to_signal(self) -> Optional[Signal]:
        """Returns None until the buffer is full (avoids partial-window FFT artifacts)."""
        if not self.is_full:
            return None
        return Signal(
            samples=self.data.copy(),
            sample_rate=self.sample_rate,
            timestamp=self.last_timestamp,
            channel=self.channel,
        )

    def __repr__(self) -> str:
        return (
            f"RollingBuffer(channel={self.channel!r}, "
            f"fill={self.n_samples}/{self.max_size}, "
            f"full={self.is_full})"
        )


def make_buffer(
    window_seconds: float, sample_rate: float, channel: str
) -> RollingBuffer:
    return RollingBuffer(
        data=np.array([], dtype=np.float64),
        max_size=int(window_seconds * sample_rate),
        sample_rate=sample_rate,
        channel=channel,
    )
