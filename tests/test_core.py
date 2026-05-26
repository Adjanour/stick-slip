import numpy as np

from stickslip.buffer import make_buffer
from stickslip.shell import csv_source, sensor_stream
from stickslip.transforms import bandpass, detrend, fft_analyze, windowed
from stickslip.types import Signal


def test_buffer_becomes_full_and_converts_to_signal():
    buf = make_buffer(1.0, 4.0, "RPM")
    buf = buf.push(np.array([1.0, 2.0]), 1.0)
    assert buf.to_signal() is None
    buf = buf.push(np.array([3.0, 4.0]), 2.0)
    sig = buf.to_signal()
    assert sig is not None
    assert sig.n_samples == 4


def test_sensor_stream_emits_both_channels_at_50hz():
    stream = sensor_stream(sample_rate=50.0, channels=("RPM", "Torque"))
    readings, timestamp = next(stream)

    assert set(readings) == {"RPM", "Torque"}
    assert isinstance(readings["RPM"], float)
    assert isinstance(readings["Torque"], float)
    assert isinstance(timestamp, float)


def test_csv_source_advances_through_bit_rpm_values():
    values = np.array([100.0, 101.5, 103.25])
    rpm = csv_source(values)
    assert rpm() == 100.0
    assert rpm() == 101.5
    assert rpm() == 103.25
    assert rpm() == 103.25


def test_fft_pipeline_returns_result():
    samples = np.sin(2 * np.pi * 1.0 * np.arange(250) / 50.0)
    sig = Signal(samples=samples, sample_rate=50.0, timestamp=1.0, channel="RPM")
    result = fft_analyze(windowed()(bandpass(0.5, 8.0)(detrend(sig))))
    assert result.peak_frequency >= 0.0
