import pytest

from stickslip.mitigation import MitigationController
from stickslip.types import (
    INTENSIFYING,
    MITIGATE,
    MINIMAL,
    STABLE,
    ENERGY_BUILDING,
    ENERGY_NORMAL,
    ENERGY_RELEASE,
    EnergyEvent,
    MitigationSignal,
    StickSlipEvent,
)


def _ss_event(status=STABLE, mi=0.0, ts=0.0):
    return StickSlipEvent(
        version="v1",
        source="test",
        timestamp=ts,
        channel="RPM",
        status=status,
        carrier_frequency=2.0,
        modulation_frequency=0.5,
        modulation_index=mi,
        growth_rate=0.0,
        sidebands_present=True,
        sidebands_growing=False,
    )


def _energy_event(status=ENERGY_NORMAL, energy=0.0, peak=0.0, drop=0.0, ts=0.0):
    return EnergyEvent(
        version="v1",
        source="test",
        timestamp=ts,
        status=status,
        energy=energy,
        peak_energy=peak,
        drop_ratio=drop,
        t_bit=0.0,
        k_total=1.0,
        bit_depth=1000.0,
    )


# ----------------------------------------------------------------


def test_starts_at_baseline_and_noop_ramp():
    signals = []
    ctrl = MitigationController(
        baseline_rpm=100.0, baseline_wob=50000.0, sink=signals.append
    )
    ctrl.on_stick_slip(_ss_event(status=STABLE, ts=1.0))
    # Current is already baseline, so no signal emitted
    assert len(signals) == 0


def test_intensifying_boosts_rpm_and_cuts_wob():
    signals = []
    ctrl = MitigationController(
        baseline_rpm=100.0, baseline_wob=50000.0, sink=signals.append
    )
    ctrl.on_stick_slip(_ss_event(status=INTENSIFYING, mi=0.8, ts=1.0))
    assert len(signals) == 1
    sig = signals[0]
    assert sig.rpm_setpoint == pytest.approx(115.0)
    assert sig.wob_setpoint == 35000.0
    assert "intensifying" in sig.reason


def test_mitigate_smaller_adjustment():
    signals = []
    ctrl = MitigationController(
        baseline_rpm=100.0, baseline_wob=50000.0, sink=signals.append
    )
    ctrl.on_stick_slip(_ss_event(status=MITIGATE, mi=0.4, ts=1.0))
    assert len(signals) == 1
    sig = signals[0]
    assert sig.rpm_setpoint == pytest.approx(110.0)
    assert sig.wob_setpoint == 40000.0


def test_ramps_back_after_mitigation():
    signals = []
    ctrl = MitigationController(
        baseline_rpm=100.0, baseline_wob=50000.0, sink=signals.append
    )
    ctrl.on_stick_slip(_ss_event(status=INTENSIFYING, mi=0.8, ts=0.0))
    assert len(signals) == 1
    assert signals[0].rpm_setpoint == pytest.approx(115.0)

    ctrl.on_stick_slip(_ss_event(status=STABLE, ts=1.0))
    assert len(signals) == 2
    sig = signals[1]
    assert sig.rpm_setpoint == pytest.approx(114.25)
    assert "ramping" in sig.reason


def test_energy_release_aggressive_wob_cut():
    signals = []
    ctrl = MitigationController(
        baseline_rpm=100.0, baseline_wob=50000.0, sink=signals.append
    )
    ctrl.on_energy(_energy_event(status=ENERGY_RELEASE, drop=0.8, ts=1.0))
    assert len(signals) == 1
    sig = signals[0]
    assert sig.wob_setpoint == 25000.0  # 50000 * 0.50
    assert sig.rpm_setpoint == 120.0  # 100 * 1.20


def test_energy_building_preemptive_wob_cut():
    signals = []
    ctrl = MitigationController(
        baseline_rpm=100.0, baseline_wob=50000.0, sink=signals.append
    )
    ctrl.on_energy(_energy_event(status=ENERGY_BUILDING, ts=1.0))
    assert len(signals) == 1
    sig = signals[0]
    assert sig.wob_setpoint == 42500.0  # 50000 * 0.85
    assert "building" in sig.reason


def test_mitigation_signal_shape():
    sig = MitigationSignal(
        version="v1",
        source="test",
        timestamp=1.0,
        rpm_setpoint=115.0,
        wob_setpoint=35000.0,
        reason="test signal",
    )
    assert sig.version == "v1"
    assert sig.rpm_setpoint == 115.0
    assert sig.wob_setpoint == 35000.0
    assert sig.reason == "test signal"
