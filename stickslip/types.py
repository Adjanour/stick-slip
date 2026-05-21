"""
Core types. All frozen - no mutation anywhere in the pure core.
numpy arrays aren't truly hashable but we treat them as values by convention:
transforms always return new Signal instances, never mutate in place.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Signal:
    samples: np.ndarray
    sample_rate: float
    timestamp: float
    channel: str

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate

    @property
    def n_samples(self) -> int:
        return len(self.samples)

    def __repr__(self) -> str:
        return (
            f"Signal(channel={self.channel!r}, "
            f"n={self.n_samples}, "
            f"rate={self.sample_rate}Hz, "
            f"t={self.timestamp:.3f})"
        )


@dataclass(frozen=True)
class FilterSpec:
    low_hz: float
    high_hz: float
    kind: str = "bandpass"
    order: int = 4


@dataclass(frozen=True)
class SpectralResult:
    frequencies: np.ndarray
    magnitudes: np.ndarray
    peak_frequency: float
    peak_magnitude: float
    severity_index: float
    timestamp: float
    channel: str

    def __repr__(self) -> str:
        return (
            f"SpectralResult(channel={self.channel!r}, "
            f"peak={self.peak_frequency:.3f}Hz, "
            f"severity={self.severity_index:.5f})"
        )


@dataclass(frozen=True)
class DisplayUpdate:
    timestamp: float
    channel: str
    peak_frequency: float
    severity_index: float
