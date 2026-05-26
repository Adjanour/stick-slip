"""
Core types — all frozen dataclasses. Transforms always return new instances.
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
    severity_index: float  # RMS of the magnitude spectrum — a spectral energy heuristic
    timestamp: float
    channel: str


@dataclass(frozen=True)
class DrillStringParams:
    shear_modulus: float
    length: float
    material_density: float

    # Torsional pendulum natural frequency: fm = 1/(2L) * sqrt(G/ρ)
    def modulation_frequency(self) -> float:
        return (1.0 / (2.0 * self.length)) * np.sqrt(
            self.shear_modulus / self.material_density
        )


@dataclass(frozen=True)
class SidebandResult:
    carrier_frequency: float
    carrier_magnitude: float
    modulation_frequency: float
    modulation_index: float  # max(sideband_ratio) — strength of FM modulation
    sidebands_present: bool
    timestamp: float
    channel: str
    # Data-oriented: parallel arrays, empty when no sidebands detected
    sb_orders: np.ndarray  # order n (1, 2, 3…)
    sb_is_upper: np.ndarray  # True = upper sideband, False = lower
    sb_expected_hz: np.ndarray  # theoretical peak location
    sb_actual_hz: np.ndarray  # actual peak location found
    sb_magnitudes: np.ndarray  # height of each sideband peak
    sb_ratios: np.ndarray  # sideband_magnitude / carrier_magnitude

    @property
    def n_sidebands(self) -> int:
        return len(self.sb_orders)


MINIMAL = "MINIMAL"
STABLE = "STABLE"
INTENSIFYING = "INTENSIFYING"
MITIGATE = "MITIGATE"


@dataclass(frozen=True)
class StickSlipAssessment:
    status: str
    carrier_frequency: float
    modulation_frequency: float
    modulation_index: float
    growth_rate: float
    sidebands_present: bool
    sidebands_growing: bool
    timestamp: float
    channel: str

    def __repr__(self) -> str:
        return (
            f"Assessment({self.status}, "
            f"fc={self.carrier_frequency:.3f}Hz, "
            f"MI={self.modulation_index:.4f}, "
            f"dMI/dt={self.growth_rate:+.5f}/s)"
        )


@dataclass(frozen=True)
class StickSlipEvent:
    version: str
    source: str
    timestamp: float
    channel: str
    status: str
    carrier_frequency: float
    modulation_frequency: float
    modulation_index: float
    growth_rate: float
    sidebands_present: bool
    sidebands_growing: bool
