"""
Torsional energy accumulation — pure functions.

Calculates elastic energy stored in the drillstring from surface torque,
accounting for temperature derating of shear modulus and series-spring
stiffness of composite BHA + drill pipe sections.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .types import (
    BHAConfig,
    ENERGY_BUILDING,
    ENERGY_NORMAL,
    ENERGY_RELEASE,
    EnergyAssessment,
    EnergyEvent,
    SegmentSpec,
    TorsionalEnergyResult,
)


# Polar moment of inertia for a hollow circular section: J = π/32 (OD⁴ - ID⁴)
def polar_moment(od: float, id: float) -> float:
    return (math.pi / 32.0) * (od**4 - id**4)


def temperature_at_depth(depth: float, surface_temp: float, gradient: float) -> float:
    return surface_temp + gradient * depth


# G drops by `derating` fraction per 100°C above surface temperature
def adjusted_G(
    g_base: float,
    temperature: float,
    surface_temp: float,
    derating: float = 0.023,
) -> float:
    delta_t = max(0.0, temperature - surface_temp)
    fraction_lost = (delta_t / 100.0) * derating
    return g_base * (1.0 - fraction_lost)


def segment_stiffness(g_adjusted: float, j_polar: float, length: float) -> float:
    if length <= 0.0:
        return math.inf
    return (g_adjusted * j_polar) / length


def build_segments(
    bit_depth: float,
    config: BHAConfig,
) -> list[dict]:
    """Assemble the drillstring bottom-up: fixed BHA components first, then drill pipe for the remaining depth."""
    segments: list[dict] = []
    remaining = bit_depth

    for comp in config.fixed_components:
        take = min(comp.length, remaining)
        if take <= 0.0:
            break
        segments.append(
            {
                "od": comp.od,
                "id": comp.id,
                "length": take,
                "bottom_depth": remaining,
                "top_depth": remaining - take,
                "label": comp.label,
            }
        )
        remaining -= take

    if remaining > 0.0:
        segments.append(
            {
                "od": config.pipe_geometry.od,
                "id": config.pipe_geometry.id,
                "length": remaining,
                "bottom_depth": remaining,
                "top_depth": 0.0,
                "label": config.pipe_geometry.label,
            }
        )

    return segments


def total_stiffness(
    segments: list[dict],
    config: BHAConfig,
) -> float:
    """Series-spring compliance: 1/K_total = Σ 1/k_i (softer segments dominate)."""
    compliance = 0.0
    for seg in segments:
        midpoint = (seg["bottom_depth"] + seg["top_depth"]) / 2.0
        temp = temperature_at_depth(
            midpoint, config.surface_temp, config.geothermal_gradient
        )
        G = adjusted_G(config.g_base, temp, config.surface_temp, config.g_temp_derating)
        J = polar_moment(seg["od"], seg["id"])
        k = segment_stiffness(G, J, seg["length"])
        compliance += 1.0 / k
    return 1.0 / compliance


def bit_torque(t_surface: float, t_off_bottom: float) -> float:
    return max(0.0, t_surface - t_off_bottom)


def angular_twist(t_bit: float, k_total: float) -> float:
    if k_total <= 0.0 or math.isinf(k_total):
        return 0.0
    return t_bit / k_total


def torsional_energy(t_bit: float, k_total: float) -> float:
    if k_total <= 0.0 or math.isinf(k_total):
        return 0.0
    return (t_bit**2) / (2.0 * k_total)


def compute_energy(
    t_surface: float,
    bit_depth: float,
    config: BHAConfig,
    t_off_bottom: Optional[float] = None,
) -> TorsionalEnergyResult:
    if t_off_bottom is None:
        t_off_bottom = config.t_off_bottom
    segments = build_segments(bit_depth, config)
    Kt = total_stiffness(segments, config)
    Tb = bit_torque(t_surface, t_off_bottom)
    theta = angular_twist(Tb, Kt)
    U = torsional_energy(Tb, Kt)

    # Temperature at bit and its effect on shear modulus
    temp_bit = temperature_at_depth(
        bit_depth, config.surface_temp, config.geothermal_gradient
    )
    g_adjusted = adjusted_G(
        config.g_base, temp_bit, config.surface_temp, config.g_temp_derating
    )
    derating_pct = (config.g_base - g_adjusted) / config.g_base * 100.0

    return TorsionalEnergyResult(
        timestamp=0.0,
        t_surface=t_surface,
        t_bit=Tb,
        k_total=Kt,
        theta=theta,
        energy=U,
        bit_depth=bit_depth,
        temp_bit=temp_bit,
        g_derating_pct=derating_pct,
        t_off_bottom=t_off_bottom,
    )


# ----------------------------------------------------------------
# Off-bottom torque tracker — dynamically recaptures T_off_bottom
# ----------------------------------------------------------------


class OffBottomTracker:
    """Tracks the off-bottom torque baseline, updating from measured torque minima.

    The spec requires recapturing T_off_bottom when the bit is off-bottom.
    In CSV mode we infer it from rolling torque minima; in live D-WIS mode
    the rig controller would signal off-bottom state explicitly.
    """

    def __init__(self, initial: float, window: int = 10, learning_rate: float = 0.3):
        self._initial = initial
        self._value = initial
        self._window = window
        self._rate = learning_rate
        self._buffer: list[float] = []

    @property
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, v: float) -> None:
        self._value = v

    def record(self, torque: float) -> None:
        """Record an explicit off-bottom torque measurement (e.g. from D-WIS off-bottom signal)."""
        self._buffer.append(torque)
        if len(self._buffer) >= self._window:
            measured = float(np.mean(self._buffer))
            self._value += self._rate * (measured - self._value)
            self._buffer.clear()

    def update_min(self, torque: float) -> None:
        """Update from rolling torque minimum heuristic (CSV/auto mode).

        When no explicit off-bottom signal exists, the minimum torque
        over a sliding window approximates the off-bottom baseline.

        Follows torque minima downward to detect off-bottom events.
        Recovers upward at a slower rate so the tracker doesn't drift
        permanently low after a transient torque drop (e.g., stick-slip release).
        """
        self._buffer.append(torque)
        if len(self._buffer) >= self._window:
            candidate = float(np.min(self._buffer))
            if candidate < self._value:
                self._value += self._rate * (candidate - self._value)
            elif candidate > self._value and self._value < self._initial:
                self._value += (self._rate * 0.25) * (candidate - self._value)
                self._value = min(self._value, self._initial)
            self._buffer.clear()

    def reset(self, value: float) -> None:
        """Force-reset to a known off-bottom torque (e.g. after a dedicated off-bottom test)."""
        self._value = value
        self._buffer.clear()


# ----------------------------------------------------------------
# Energy history tracker — ring buffer for trend analysis
# ----------------------------------------------------------------


class EnergyHistory:
    """Ring-buffer tracking energy buildup/release for trend assessment.

    Detects the characteristic stick-slip energy signature: gradual torsional
    accumulation followed by a sharp release (sudden drop ≥ drop_threshold).
    """

    def __init__(self, capacity: int = 10, drop_threshold: float = 0.50):
        self._capacity = capacity
        self._drop_threshold = drop_threshold
        self._energies: list[float] = []
        self._timestamps: list[float] = []

    def update(self, result: TorsionalEnergyResult) -> None:
        """Append energy sample; evicts oldest when at capacity."""
        self._energies.append(result.energy)
        self._timestamps.append(result.timestamp)
        if len(self._energies) > self._capacity:
            self._energies.pop(0)
            self._timestamps.pop(0)

    @property
    def filled(self) -> int:
        return len(self._energies)

    @property
    def is_warm(self) -> bool:
        return self.filled >= self._capacity

    @property
    def peak_energy(self) -> float:
        return max(self._energies) if self._energies else 0.0

    @property
    def current_energy(self) -> float:
        return self._energies[-1] if self._energies else 0.0

    @property
    def drop_ratio(self) -> float:
        peak = self.peak_energy
        if peak <= 0.0:
            return 0.0
        return (peak - self.current_energy) / peak

    @property
    def has_sharp_drop(self) -> bool:
        if not self.is_warm:
            return False
        return self.drop_ratio >= self._drop_threshold

    def assess(self, timestamp: float) -> EnergyAssessment:
        """Classify current trend: NORMAL, BUILDING, or RELEASE (sharp drop ≥ threshold)."""
        if not self.is_warm:
            return EnergyAssessment(
                status=ENERGY_NORMAL,
                energy=self.current_energy,
                peak_energy=self.peak_energy,
                drop_ratio=self.drop_ratio,
                timestamp=timestamp,
            )

        if self.has_sharp_drop:
            return EnergyAssessment(
                status=ENERGY_RELEASE,
                energy=self.current_energy,
                peak_energy=self.peak_energy,
                drop_ratio=self.drop_ratio,
                timestamp=timestamp,
            )

        if self.filled >= 3:
            recent = self._energies[-3:]
            if recent[-1] > recent[0]:
                return EnergyAssessment(
                    status=ENERGY_BUILDING,
                    energy=self.current_energy,
                    peak_energy=self.peak_energy,
                    drop_ratio=self.drop_ratio,
                    timestamp=timestamp,
                )

        return EnergyAssessment(
            status=ENERGY_NORMAL,
            energy=self.current_energy,
            peak_energy=self.peak_energy,
            drop_ratio=self.drop_ratio,
            timestamp=timestamp,
        )


DEFAULT_BHA = BHAConfig(
    g_base=79e9,
    surface_temp=25.0,
    geothermal_gradient=0.03,
    t_off_bottom=5000.0,
    fixed_components=(
        SegmentSpec(od=0.171, id=0.071, length=120.0, label="drill_collar"),
        SegmentSpec(od=0.127, id=0.076, length=200.0, label="hwdp"),
    ),
    pipe_geometry=SegmentSpec(od=0.127, id=0.108, length=0.0, label="drill_pipe"),
)
