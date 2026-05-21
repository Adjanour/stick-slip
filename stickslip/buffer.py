"""
Immutable rolling window buffer.

State is modelled explicitly: push() returns a NEW buffer.
No mutation, no hidden state
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .types import Signal


@dataclass(frozen=True)
class RollingBuffer:
    """Immutable rolling sample window for one channel."""

    data: tuple
    max_size: int
    sample_rate: float
    channel: str
    last_timestamp: float = 0.0

    @property
    def is_full(self) -> bool:
        """Return True when the buffer has reached its configured capacity."""
        return len(self.data) >= self.max_size

    @property
    def fill_fraction(self) -> float:
        """Return the fraction of the window currently filled."""
        return len(self.data) / self.max_size

    @property
    def n_samples(self) -> int:
        """Return the current number of stored samples."""
        return len(self.data)

    def push(self, new_samples: np.ndarray, timestamp: float) -> "RollingBuffer":
        """Return a new buffer with the incoming samples appended."""
        combined = np.concatenate([np.array(self.data, dtype=np.float64), new_samples])
        trimmed = combined[-self.max_size :]
        return RollingBuffer(
            data=tuple(trimmed),
            max_size=self.max_size,
            sample_rate=self.sample_rate,
            channel=self.channel,
            last_timestamp=timestamp,
        )

    def to_signal(self) -> Optional[Signal]:
        """Convert the full buffer into a Signal, or None if still filling."""
        if not self.is_full:
            return None
        return Signal(
            samples=np.array(self.data, dtype=np.float64),
            sample_rate=self.sample_rate,
            timestamp=self.last_timestamp,
            channel=self.channel,
        )

    def __repr__(self) -> str:
        """Return a compact debug representation."""
        return (
            f"RollingBuffer(channel={self.channel!r}, "
            f"fill={self.n_samples}/{self.max_size}, "
            f"full={self.is_full})"
        )


def make_buffer(
    window_seconds: float, sample_rate: float, channel: str
) -> RollingBuffer:
    """Create an empty rolling buffer sized for a fixed time window."""
    return RollingBuffer(
        data=tuple(),
        max_size=int(window_seconds * sample_rate),
        sample_rate=sample_rate,
        channel=channel,
    )
