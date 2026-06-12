import numpy as np
from pathlib import Path

from stickslip.buffer import make_buffer
from stickslip.shell import SharedCsvSource
from stickslip.transforms import bandpass, detrend, fft_analyze, windowed
from stickslip.types import Signal


def test_buffer_becomes_full_and_converts_to_signal():
    buf = make_buffer(1.0, 4.0, "RPM")
    assert buf.to_signal() is None
    buf.push(np.array([1.0, 2.0]), 1.0)
    assert buf.to_signal() is None
    buf.push(np.array([3.0, 4.0]), 2.0)
    sig = buf.to_signal()
    assert sig is not None
    assert sig.n_samples == 4


def test_csv_source_advances_through_bit_rpm_values():
    csv_path = Path(__file__).resolve().parent.parent / "test.csv"
    source = SharedCsvSource(path=csv_path, chunk_size=10, sample_rate=50.0)
    assert source.total_values > 0
    rpm = source.next_rpm()
    assert len(rpm) == 10
    torque = source.next_torque()
    assert len(torque) == 10


def test_fft_pipeline_returns_result():
    samples = np.sin(2 * np.pi * 1.0 * np.arange(250) / 50.0)
    sig = Signal(samples=samples, sample_rate=50.0, timestamp=1.0, channel="RPM")
    result = fft_analyze(windowed()(bandpass(1.0, 5.0)(detrend(sig))))
    assert result.peak_frequency >= 0.0
