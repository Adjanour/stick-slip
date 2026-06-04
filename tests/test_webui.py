"""Smoke tests for the FastHTML web UI module."""

import threading
import time

from stickslip.types import (
    ENERGY_NORMAL,
    EnergyEvent,
    MitigationSignal,
    StickSlipEvent,
)
from stickslip.webui import SharedState


def test_shared_state_initial():
    s = SharedState()
    ss, en, m, events = s.snapshot()
    assert ss is None
    assert en is None
    assert m is None
    assert events == []


def test_shared_state_update_ss():
    s = SharedState()
    event = StickSlipEvent(
        version="v1",
        source="test",
        timestamp=1.0,
        channel="RPM",
        status="INTENSIFYING",
        carrier_frequency=2.0,
        modulation_frequency=0.5,
        modulation_index=0.8,
        growth_rate=0.1,
        sidebands_present=True,
        sidebands_growing=True,
    )
    s.update_ss(event)
    ss, _, _, events = s.snapshot()
    assert ss is event
    assert len(events) == 1
    assert events[0][1] == "SB"


def test_shared_state_update_energy():
    s = SharedState()
    event = EnergyEvent(
        version="v1",
        source="test",
        timestamp=2.0,
        status="ENERGY_RELEASE",
        energy=100.0,
        peak_energy=500.0,
        drop_ratio=0.8,
        t_bit=7000.0,
        k_total=1183.0,
        bit_depth=1000.0,
    )
    s.update_energy(event)
    _, en, _, events = s.snapshot()
    assert en is event
    assert len(events) == 1
    assert events[0][1] == "EN"


def test_shared_state_update_mitigation():
    s = SharedState()
    signal = MitigationSignal(
        version="v1",
        source="test",
        timestamp=3.0,
        rpm_setpoint=115.0,
        wob_setpoint=35000.0,
        reason="test signal",
    )
    s.update_mitigation(signal)
    _, _, m, events = s.snapshot()
    assert m is signal
    assert len(events) == 1
    assert events[0][1] == "CTRL"
    assert "test signal" in events[0][2]


def test_shared_state_thread_safety():
    s = SharedState()
    results = []

    def writer():
        for i in range(100):
            ev = StickSlipEvent(
                version="v1",
                source="test",
                timestamp=float(i),
                channel="RPM",
                status="MINIMAL",
                carrier_frequency=0.0,
                modulation_frequency=0.0,
                modulation_index=0.0,
                growth_rate=0.0,
                sidebands_present=False,
                sidebands_growing=False,
            )
            s.update_ss(ev)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ss, _, _, events = s.snapshot()
    assert ss is not None
    assert len(events) <= 50


def test_shared_state_running_flag():
    s = SharedState()
    assert not s.running
    s.running = True
    assert s.running
    s.running = False
    assert not s.running
