from stickslip.dashboard import Dashboard
from stickslip.types import (
    INTENSIFYING,
    EnergyEvent,
    MitigationSignal,
    StickSlipEvent,
)


def test_dashboard_handles_ss_event():
    d = Dashboard()
    event = StickSlipEvent(
        version="v1",
        source="test",
        timestamp=1.0,
        channel="RPM",
        status=INTENSIFYING,
        carrier_frequency=2.0,
        modulation_frequency=0.5,
        modulation_index=0.8,
        growth_rate=0.1,
        sidebands_present=True,
        sidebands_growing=True,
    )
    d.on_stick_slip(event)
    assert d._ss is event


def test_dashboard_handles_energy_event():
    d = Dashboard()
    event = EnergyEvent(
        version="v1",
        source="test",
        timestamp=1.0,
        status="ENERGY_RELEASE",
        energy=100.0,
        peak_energy=500.0,
        drop_ratio=0.8,
        t_bit=7000.0,
        k_total=1183.0,
        bit_depth=1000.0,
    )
    d.on_energy(event)
    assert d._energy is event


def test_dashboard_handles_mitigation_signal():
    d = Dashboard()
    sig = MitigationSignal(
        version="v1",
        source="test",
        timestamp=1.0,
        rpm_setpoint=115.0,
        wob_setpoint=35000.0,
        reason="test",
    )
    d.on_mitigation(sig)
    assert d._mitigation is sig


def test_dashboard_renders_without_error():
    d = Dashboard()
    layout = d.__rich__()
    assert layout is not None


def test_dashboard_logs_events():
    d = Dashboard(max_events=5)
    for i in range(10):
        d.on_stick_slip(
            StickSlipEvent(
                version="v1",
                source="test",
                timestamp=float(i),
                channel="RPM",
                status=INTENSIFYING,
                carrier_frequency=2.0,
                modulation_frequency=0.5,
                modulation_index=0.1 * i,
                growth_rate=0.0,
                sidebands_present=True,
                sidebands_growing=False,
            )
        )
    assert len(d._events) == 5  # maxlen honoured
