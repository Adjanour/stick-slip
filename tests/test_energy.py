import math

import numpy as np
import pytest

from stickslip.energy import (
    DEFAULT_BHA,
    EnergyHistory,
    adjusted_G,
    angular_twist,
    bit_torque,
    build_segments,
    compute_energy,
    polar_moment,
    segment_stiffness,
    temperature_at_depth,
    torsional_energy,
    total_stiffness,
)
from stickslip.types import (
    BHAConfig,
    ENERGY_BUILDING,
    ENERGY_NORMAL,
    ENERGY_RELEASE,
    EnergyEvent,
    SegmentSpec,
)


def test_polar_moment_solid_rod():
    # Solid rod: ID=0 → J = π/32 * OD^4
    J = polar_moment(0.1, 0.0)
    expected = (math.pi / 32.0) * (0.1**4)
    assert J == pytest.approx(expected)


def test_polar_moment_hollow():
    od, id = 0.171, 0.071
    J = polar_moment(od, id)
    expected = (math.pi / 32.0) * (od**4 - id**4)
    assert J == pytest.approx(expected)


def test_temperature_at_depth():
    T = temperature_at_depth(2000.0, 25.0, 0.03)
    assert T == pytest.approx(85.0)


def test_temperature_at_surface():
    T = temperature_at_depth(0.0, 25.0, 0.03)
    assert T == 25.0


def test_adjusted_G_no_derating():
    G = adjusted_G(79e9, 25.0, 25.0)
    assert G == 79e9


def test_adjusted_G_partial_derating():
    # 100°C above surface → 2.3% loss
    G = adjusted_G(79e9, 125.0, 25.0)
    expected = 79e9 * (1.0 - 0.023)
    assert G == pytest.approx(expected)


def test_segment_stiffness_normal():
    G, J, L = 79e9, 1e-4, 100.0
    k = segment_stiffness(G, J, L)
    assert k == pytest.approx((G * J) / L)


def test_segment_stiffness_zero_length():
    k = segment_stiffness(79e9, 1e-4, 0.0)
    assert k == math.inf


def test_build_segments_deeper_than_bha():
    depth = 1500.0
    config = BHAConfig(
        g_base=79e9,
        fixed_components=(
            SegmentSpec(od=0.171, id=0.071, length=120.0, label="drill_collar"),
            SegmentSpec(od=0.127, id=0.076, length=200.0, label="hwdp"),
        ),
        pipe_geometry=SegmentSpec(od=0.127, id=0.108, length=0.0, label="drill_pipe"),
    )
    segs = build_segments(depth, config)
    assert len(segs) == 3
    assert segs[0]["label"] == "drill_collar"
    assert segs[1]["label"] == "hwdp"
    assert segs[2]["label"] == "drill_pipe"
    assert segs[2]["length"] == pytest.approx(1500.0 - 120.0 - 200.0)
    assert segs[2]["top_depth"] == 0.0


def test_build_segments_shallower_than_bha():
    depth = 50.0
    config = BHAConfig(
        fixed_components=(
            SegmentSpec(od=0.171, id=0.071, length=120.0, label="drill_collar"),
        ),
    )
    segs = build_segments(depth, config)
    assert len(segs) == 1
    assert segs[0]["length"] == pytest.approx(50.0)


def test_total_stiffness_finite():
    config = BHAConfig(
        g_base=79e9,
        surface_temp=25.0,
        fixed_components=(
            SegmentSpec(od=0.171, id=0.071, length=10.0, label="collar"),
        ),
        pipe_geometry=SegmentSpec(od=0.127, id=0.108, length=0.0, label="pipe"),
    )
    segs = build_segments(100.0, config)
    Kt = total_stiffness(segs, config)
    assert Kt > 0.0
    assert math.isfinite(Kt)


def test_bit_torque_no_off_bottom():
    Tb = bit_torque(10000.0, 5000.0)
    assert Tb == 5000.0


def test_bit_torque_clamped():
    Tb = bit_torque(3000.0, 5000.0)
    assert Tb == 0.0


def test_angular_twist_normal():
    theta = angular_twist(5000.0, 1e4)
    assert theta == pytest.approx(0.5)


def test_angular_twist_zero_stiffness():
    assert angular_twist(5000.0, 0.0) == 0.0
    assert angular_twist(5000.0, math.inf) == 0.0


def test_torsional_energy_normal():
    U = torsional_energy(5000.0, 1e4)
    assert U == pytest.approx(5000.0**2 / (2.0 * 1e4))


def test_torsional_energy_zero_stiffness():
    assert torsional_energy(5000.0, 0.0) == 0.0
    assert torsional_energy(5000.0, math.inf) == 0.0


def test_compute_energy_end_to_end():
    result = compute_energy(t_surface=10000.0, bit_depth=1000.0, config=DEFAULT_BHA)
    assert result.energy > 0.0
    assert result.k_total > 0.0
    assert result.t_bit > 0.0
    assert result.theta > 0.0
    assert result.bit_depth == 1000.0


def test_compute_energy_no_bit_torque():
    result = compute_energy(t_surface=1000.0, bit_depth=1000.0, config=DEFAULT_BHA)
    assert result.t_bit == 0.0
    assert result.energy == 0.0
    assert result.theta == 0.0


# ----------------------------------------------------------------
# EnergyHistory
# ----------------------------------------------------------------


def test_energy_history_not_warm_when_empty():
    h = EnergyHistory(capacity=5)
    assert not h.is_warm
    assert h.filled == 0
    assert h.peak_energy == 0.0
    assert h.current_energy == 0.0
    assert h.drop_ratio == 0.0
    assert not h.has_sharp_drop


def test_energy_history_becomes_warm():
    h = EnergyHistory(capacity=3)
    for i in range(3):
        h.update(_result(energy=float(i + 1), timestamp=float(i)))
    assert h.is_warm
    assert h.filled == 3


def test_energy_history_rings():
    h = EnergyHistory(capacity=3)
    for i in range(6):
        h.update(_result(energy=float(i + 1), timestamp=float(i)))
    assert h.filled == 3
    assert h.is_warm
    assert h.peak_energy == 6.0


def test_energy_history_peak_and_current():
    h = EnergyHistory(capacity=5)
    vals = [1.0, 3.0, 2.0, 5.0, 4.0]
    for i, v in enumerate(vals):
        h.update(_result(energy=v, timestamp=float(i)))
    assert h.peak_energy == 5.0
    assert h.current_energy == 4.0


def test_energy_history_drop_ratio():
    h = EnergyHistory(capacity=5)
    for i in range(5):
        h.update(_result(energy=10.0, timestamp=float(i)))
    h.update(_result(energy=2.0, timestamp=5.0))
    assert h.drop_ratio == pytest.approx(0.8)
    assert h.has_sharp_drop


def test_energy_history_assess_normal_when_not_warm():
    h = EnergyHistory(capacity=5)
    h.update(_result(energy=10.0, timestamp=0.0))
    a = h.assess(0.0)
    assert a.status == ENERGY_NORMAL


def test_energy_history_assess_release_on_sharp_drop():
    h = EnergyHistory(capacity=5, drop_threshold=0.50)
    for i in range(5):
        h.update(_result(energy=100.0, timestamp=float(i)))
    h.update(_result(energy=10.0, timestamp=5.0))
    a = h.assess(5.0)
    assert a.status == ENERGY_RELEASE
    assert a.drop_ratio >= 0.50


def test_energy_history_assess_building():
    h = EnergyHistory(capacity=5)
    for i in range(5):
        h.update(_result(energy=float(i + 1) * 10.0, timestamp=float(i)))
    a = h.assess(4.0)
    assert a.status == ENERGY_BUILDING


def test_energy_history_assess_normal_when_steady():
    h = EnergyHistory(capacity=5)
    for i in range(5):
        h.update(_result(energy=50.0, timestamp=float(i)))
    a = h.assess(4.0)
    assert a.status == ENERGY_NORMAL


def test_energy_event_shape():
    event = EnergyEvent(
        version="v1",
        source="test",
        timestamp=1.0,
        status=ENERGY_RELEASE,
        energy=100.0,
        peak_energy=500.0,
        drop_ratio=0.8,
        t_bit=3000.0,
        k_total=1e5,
        bit_depth=1000.0,
    )
    assert event.version == "v1"
    assert event.status == ENERGY_RELEASE
    assert event.drop_ratio == 0.8


def test_energy_event_temperature_fields():
    event = EnergyEvent(
        version="v1",
        source="test",
        timestamp=1.0,
        status=ENERGY_RELEASE,
        energy=100.0,
        peak_energy=500.0,
        drop_ratio=0.8,
        t_bit=3000.0,
        k_total=1e5,
        bit_depth=1000.0,
        temp_bit=55.0,
        g_derating_pct=0.69,
    )
    assert event.temp_bit == 55.0
    assert event.g_derating_pct == 0.69


def test_compute_energy_returns_temperature():
    from stickslip.energy import DEFAULT_BHA, compute_energy

    result = compute_energy(t_surface=6000.0, bit_depth=1000.0, config=DEFAULT_BHA)
    assert result.temp_bit == 55.0  # 25 + 0.03*1000
    assert 0.69 <= result.g_derating_pct <= 0.70
    assert result.energy > 0.0


def test_compute_energy_temperature_shallow():
    from stickslip.energy import DEFAULT_BHA, compute_energy

    result = compute_energy(t_surface=5000.0, bit_depth=10.0, config=DEFAULT_BHA)
    assert result.temp_bit == 25.3  # 25 + 0.03*10
    assert result.g_derating_pct < 0.01  # negligible derating at shallow depth


def test_compute_energy_temperature_at_depth():
    from stickslip.energy import DEFAULT_BHA, compute_energy

    result = compute_energy(t_surface=5000.0, bit_depth=2000.0, config=DEFAULT_BHA)
    assert result.temp_bit == 85.0  # 25 + 0.03*2000
    assert result.g_derating_pct > 0.69  # more derating at higher temp


def test_default_bha_is_reasonable():
    assert DEFAULT_BHA.g_base == 79e9
    assert len(DEFAULT_BHA.fixed_components) == 2
    assert DEFAULT_BHA.fixed_components[0].label == "drill_collar"
    assert DEFAULT_BHA.fixed_components[1].label == "hwdp"
    assert DEFAULT_BHA.pipe_geometry.label == "drill_pipe"
    assert DEFAULT_BHA.t_off_bottom == 5000.0


# ----------------------------------------------------------------
# helpers
# ----------------------------------------------------------------


# ----------------------------------------------------------------
# OffBottomTracker tests
# ----------------------------------------------------------------


def test_off_tracker_initial_value():
    from stickslip.energy import OffBottomTracker

    t = OffBottomTracker(initial=5000.0)
    assert t.value == 5000.0


def test_off_tracker_record_averages():
    from stickslip.energy import OffBottomTracker

    t = OffBottomTracker(initial=5000.0, window=3, learning_rate=1.0)
    t.record(4000.0)
    assert t.value == 5000.0  # not yet full
    t.record(4200.0)
    assert t.value == 5000.0
    t.record(4100.0)
    assert t.value == 4100.0  # mean(4000,4200,4100) = 4100, rate=1 → jump


def test_off_tracker_record_smoothing():
    from stickslip.energy import OffBottomTracker

    t = OffBottomTracker(initial=5000.0, window=2, learning_rate=0.5)
    t.record(4000.0)
    t.record(4000.0)
    # mean=4000, value = 5000 + 0.5*(4000-5000) = 4500
    assert abs(t.value - 4500.0) < 0.01


def test_off_tracker_update_min():
    from stickslip.energy import OffBottomTracker

    t = OffBottomTracker(initial=5000.0, window=3, learning_rate=1.0)
    t.update_min(4800.0)
    t.update_min(4900.0)
    t.update_min(3200.0)  # min=3200 < 5000 → update
    assert t.value == 3200.0


def test_off_tracker_update_min_no_change_if_above():
    from stickslip.energy import OffBottomTracker

    t = OffBottomTracker(initial=3000.0, window=2, learning_rate=1.0)
    t.update_min(3500.0)
    t.update_min(4000.0)  # min=3500 > 3000 → no update
    assert t.value == 3000.0


def test_off_tracker_reset():
    from stickslip.energy import OffBottomTracker

    t = OffBottomTracker(initial=5000.0, window=2, learning_rate=1.0)
    t.record(4000.0)
    t.record(4000.0)  # window filled → update = 5000 + 1.0*(4000-5000) = 4000
    assert t.value == 4000.0
    t.reset(5000.0)
    assert t.value == 5000.0


def test_compute_energy_accepts_dynamic_t_off_bottom():
    from stickslip.energy import DEFAULT_BHA, compute_energy

    result = compute_energy(
        t_surface=6000.0, bit_depth=1000.0, config=DEFAULT_BHA, t_off_bottom=3000.0
    )
    assert result.t_off_bottom == 3000.0
    # T_bit = 6000 - 3000 = 3000 (vs 6000 - 5000 = 1000 with default)
    assert result.t_bit > 1000.0
    assert result.energy > 0.0


def test_compute_energy_defaults_to_config_t_off_bottom():
    from stickslip.energy import DEFAULT_BHA, compute_energy

    result = compute_energy(t_surface=6000.0, bit_depth=1000.0, config=DEFAULT_BHA)
    assert result.t_off_bottom == 5000.0
    assert result.t_bit == 1000.0  # 6000 - 5000


def _result(energy: float, timestamp: float):
    from stickslip.types import TorsionalEnergyResult

    return TorsionalEnergyResult(
        timestamp=timestamp,
        t_surface=0.0,
        t_bit=0.0,
        k_total=1.0,
        theta=0.0,
        energy=energy,
        bit_depth=0.0,
    )
