"""
Mitigation controller — consumes detection events, emits setpoint adjustments.
"""

from __future__ import annotations

from typing import Callable

from .types import (
    ENERGY_BUILDING,
    ENERGY_RELEASE,
    EnergyEvent,
    INTENSIFYING,
    MINIMAL,
    MITIGATE,
    MitigationSignal,
    STABLE,
    StickSlipEvent,
)

MitigationSink = Callable[[MitigationSignal], None]


class MitigationController:
    """Subscribes to both detection tracks and emits combined setpoint adjustments.

    Two-track strategy:
      - RPM boost + WOB cut on sideband intensification/mitigation.
      - Aggressive WOB cut + RPM boost on energy release (torsional vibration burst).
    """

    def __init__(
        self,
        baseline_rpm: float,
        baseline_wob: float,
        sink: MitigationSink,
        source: str = "mitigation-controller",
        version: str = "v1",
        rpm_boost: float = 1.15,
        wob_cut: float = 0.70,
        energy_wob_cut: float = 0.50,
        ramp_step: float = 0.05,
        mitigate_rpm_boost: float = 1.10,
        mitigate_wob_cut: float = 0.80,
        energy_rpm_boost: float = 1.20,
        energy_building_wob_cut: float = 0.85,
    ):
        self._baseline_rpm = baseline_rpm
        self._baseline_wob = baseline_wob
        self._sink = sink
        self._source = source
        self._version = version
        self._rpm_boost = rpm_boost
        self._wob_cut = wob_cut
        self._mitigate_rpm_boost = mitigate_rpm_boost
        self._mitigate_wob_cut = mitigate_wob_cut
        self._energy_rpm_boost = energy_rpm_boost
        self._energy_wob_cut = energy_wob_cut
        self._energy_building_wob_cut = energy_building_wob_cut
        self._ramp_step = ramp_step
        self._current_rpm = baseline_rpm
        self._current_wob = baseline_wob

    def _emit(self, rpm: float, wob: float, reason: str, timestamp: float) -> None:
        self._sink(
            MitigationSignal(
                version=self._version,
                source=self._source,
                timestamp=timestamp,
                rpm_setpoint=rpm,
                wob_setpoint=wob,
                reason=reason,
            )
        )
        self._current_rpm = rpm
        self._current_wob = wob

    def _ramp_to_baseline(self, ts: float) -> None:
        drpm = self._baseline_rpm - self._current_rpm
        dwob = self._baseline_wob - self._current_wob
        if abs(drpm) < 0.01 and abs(dwob) < 0.01:
            return
        rpm = self._current_rpm + drpm * self._ramp_step
        wob = self._current_wob + dwob * self._ramp_step
        self._emit(rpm, wob, "ramping to baseline", timestamp=ts)

    def on_stick_slip(self, event: StickSlipEvent) -> None:
        """React to sideband-based assessment: INTENSIFYING/MITIGATE → boost RPM + cut WOB."""
        ts = event.timestamp
        if event.status == INTENSIFYING:
            self._emit(
                self._baseline_rpm * self._rpm_boost,
                self._baseline_wob * self._wob_cut,
                f"stick-slip intensifying (MI={event.modulation_index:.3f})",
                timestamp=ts,
            )
        elif event.status == MITIGATE:
            self._emit(
                self._baseline_rpm * self._mitigate_rpm_boost,
                self._baseline_wob * self._mitigate_wob_cut,
                f"stick-slip mitigation (MI={event.modulation_index:.3f})",
                timestamp=ts,
            )
        elif event.status in (STABLE, MINIMAL):
            self._ramp_to_baseline(ts)

    def on_energy(self, event: EnergyEvent) -> None:
        """React to energy-based assessment: RELEASE → aggressive WOB cut, BUILDING → pre-emptive cut."""
        ts = event.timestamp
        if event.status == ENERGY_RELEASE:
            self._emit(
                self._baseline_rpm * self._energy_rpm_boost,
                self._baseline_wob * self._energy_wob_cut,
                f"energy release detected (drop={event.drop_ratio:.0%})",
                timestamp=ts,
            )
        elif event.status == ENERGY_BUILDING:
            self._emit(
                self._current_rpm,
                self._baseline_wob * self._energy_building_wob_cut,
                "energy building — pre-emptive WOB reduction",
                timestamp=ts,
            )
