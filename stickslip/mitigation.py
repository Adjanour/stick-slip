"""
Mitigation controller — consumes detection events, emits setpoint adjustments.

Priority-based fusion of sideband + energy tracks:

  1. MITIGATE (sideband)       → highest: stick-slip actively happening
  2. ENERGY_RELEASE            → torsional burst released — aggressive WOB cut
  3. INTENSIFYING (sideband)   → preventive RPM boost + WOB cut
  4. ENERGY_BUILDING           → pre-emptive WOB reduction (preserves RPM)
  5. STABLE / MINIMAL / NORMAL → ramp to baseline
"""

from __future__ import annotations

from typing import Callable, Optional

from .types import (
    ENERGY_BUILDING,
    ENERGY_NORMAL,
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

# Priority values: higher = more urgent
_PRIORITY: dict[str, int] = {
    MITIGATE: 50,
    ENERGY_RELEASE: 40,
    INTENSIFYING: 30,
    ENERGY_BUILDING: 20,
    STABLE: 10,
    MINIMAL: 10,
    ENERGY_NORMAL: 10,
}


class MitigationController:
    """Subscribes to both detection tracks and emits combined setpoint adjustments.

    Maintains the latest status from each track and fuses them by priority
    on every call, so the more urgent action always wins.
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
        self._ss_status: str = MINIMAL
        self._energy_status: str = ENERGY_NORMAL
        self._ss_timestamp: float = 0.0
        self._energy_timestamp: float = 0.0

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

    def _fusion(self, ts: float) -> None:
        """Fuse the latest sideband + energy status by priority and emit."""
        ss_priority = _PRIORITY.get(self._ss_status, 0)
        en_priority = _PRIORITY.get(self._energy_status, 0)

        if ss_priority >= en_priority:
            if self._ss_status == MITIGATE:
                self._emit(
                    self._baseline_rpm * self._mitigate_rpm_boost,
                    self._baseline_wob * self._mitigate_wob_cut,
                    f"stick-slip mitigation (MI, priority={ss_priority})",
                    timestamp=ts,
                )
                return
            if self._ss_status == INTENSIFYING:
                self._emit(
                    self._baseline_rpm * self._rpm_boost,
                    self._baseline_wob * self._wob_cut,
                    f"stick-slip intensifying (MI, priority={ss_priority})",
                    timestamp=ts,
                )
                return
            if self._ss_status in (STABLE, MINIMAL) and en_priority <= 10:
                self._ramp_to_baseline(ts)
                return

        # Energy priority is higher or equal — apply energy action
        if self._energy_status == ENERGY_RELEASE:
            self._emit(
                self._baseline_rpm * self._energy_rpm_boost,
                self._baseline_wob * self._energy_wob_cut,
                f"energy release detected (priority={en_priority})",
                timestamp=ts,
            )
            return
        if self._energy_status == ENERGY_BUILDING:
            self._emit(
                self._current_rpm,
                self._baseline_wob * self._energy_building_wob_cut,
                f"energy building — pre-emptive WOB reduction (priority={en_priority})",
                timestamp=ts,
            )
            return

        # Fallback: sideband action with lower priority
        if self._ss_status == MITIGATE:
            self._emit(
                self._baseline_rpm * self._mitigate_rpm_boost,
                self._baseline_wob * self._mitigate_wob_cut,
                f"stick-slip mitigation (MI, priority={ss_priority})",
                timestamp=ts,
            )
            return
        if self._ss_status == INTENSIFYING:
            self._emit(
                self._baseline_rpm * self._rpm_boost,
                self._baseline_wob * self._wob_cut,
                f"stick-slip intensifying (MI, priority={ss_priority})",
                timestamp=ts,
            )
            return
        if self._ss_status in (STABLE, MINIMAL):
            self._ramp_to_baseline(ts)
            return

        self._ramp_to_baseline(ts)

    def on_stick_slip(self, event: StickSlipEvent) -> None:
        self._ss_status = event.status
        self._ss_timestamp = event.timestamp
        self._fusion(event.timestamp)

    def on_energy(self, event: EnergyEvent) -> None:
        self._energy_status = event.status
        self._energy_timestamp = event.timestamp
        self._fusion(event.timestamp)
