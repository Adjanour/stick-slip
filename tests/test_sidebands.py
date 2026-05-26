import numpy as np

from stickslip.assessment import assess
from stickslip.history import ModulationHistory
from stickslip.sidebands import compute_fm, detect_sidebands
from stickslip.types import (
    DrillStringParams,
    INTENSIFYING,
    MITIGATE,
    MINIMAL,
    STABLE,
    SidebandResult,
    Signal,
    StickSlipEvent,
)
from stickslip.transforms import bandpass, detrend, fft_analyze, windowed


RATE = 50.0


def _signal(fc: float = 2.0, fm: float = 0.5) -> Signal:
    t = np.linspace(0, 5.0, int(5.0 * RATE), endpoint=False)
    samples = 10.0 * np.sin(2 * np.pi * fc * t)
    samples += 3.0 * np.sin(2 * np.pi * (fc + fm) * t)
    samples += 3.0 * np.sin(2 * np.pi * (fc - fm) * t)
    return Signal(samples=samples, sample_rate=RATE, timestamp=0.0, channel="RPM")


def _spectral():
    sig = _signal()
    return fft_analyze(windowed()(bandpass(0.5, 8.0)(detrend(sig))))


def test_compute_fm_matches_params():
    params = DrillStringParams(80e9, 1000.0, 7850.0)
    assert compute_fm(params) == params.modulation_frequency()


def test_detects_sidebands():
    result = detect_sidebands(_spectral(), fm=0.5, n_max=1)
    assert result.sidebands_present


def test_history_growth_rate_and_assessment():
    history = ModulationHistory(capacity=10)
    spectral = _spectral()
    base = detect_sidebands(spectral, fm=0.5, n_max=1)

    for i in range(3):
        result = SidebandResult(
            carrier_frequency=base.carrier_frequency,
            carrier_magnitude=base.carrier_magnitude,
            modulation_frequency=base.modulation_frequency,
            modulation_index=0.01 * i,
            sidebands_present=base.sidebands_present,
            timestamp=float(i),
            channel=base.channel,
            sb_orders=base.sb_orders,
            sb_is_upper=base.sb_is_upper,
            sb_expected_hz=base.sb_expected_hz,
            sb_actual_hz=base.sb_actual_hz,
            sb_magnitudes=base.sb_magnitudes,
            sb_ratios=base.sb_ratios,
        )
        history.update(result)

    assert history.growth_rate() >= 0.0

    assessed = assess(result, growth_rate=0.01, is_growing=True)
    assert assessed.status in {INTENSIFYING, MITIGATE, STABLE, MINIMAL}


def test_stick_slip_event_shape():
    event = StickSlipEvent(
        version="v1",
        source="test",
        timestamp=1.0,
        channel="RPM",
        status=MINIMAL,
        carrier_frequency=2.0,
        modulation_frequency=0.5,
        modulation_index=0.0,
        growth_rate=0.0,
        sidebands_present=False,
        sidebands_growing=False,
    )
    assert event.version == "v1"
    assert event.source == "test"
