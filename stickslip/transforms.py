"""
Pure signal transforms.
"""

from __future__ import annotations

from functools import partial
from typing import Callable

import numpy as np
from scipy import signal as scipy_signal

from .types import FilterSpec, Signal, SpectralResult


def detrend(sig: Signal) -> Signal:
    """Remove linear trend and DC drift from a signal."""
    return _replace_samples(sig, scipy_signal.detrend(sig.samples, type="linear"))


def _build_sos(spec: FilterSpec, sample_rate: float) -> np.ndarray:
    """Build a numerically stable SOS filter for the given specification."""
    nyq = sample_rate / 2.0
    if spec.kind == "bandpass":
        return scipy_signal.butter(
            spec.order,
            [spec.low_hz / nyq, spec.high_hz / nyq],
            btype="bandpass",
            output="sos",
        )
    if spec.kind == "lowpass":
        return scipy_signal.butter(
            spec.order,
            spec.high_hz / nyq,
            btype="lowpass",
            output="sos",
        )
    raise ValueError(f"Unknown filter kind: {spec.kind!r}")


def apply_filter(spec: FilterSpec, sig: Signal) -> Signal:
    """Apply a Butterworth filter without mutating the input signal."""
    sos = _build_sos(spec, sig.sample_rate)
    return _replace_samples(sig, scipy_signal.sosfiltfilt(sos, sig.samples))


def apply_window_fn(window_name: str, sig: Signal) -> Signal:
    """Apply a spectral window before FFT analysis."""
    w = scipy_signal.get_window(window_name, sig.n_samples)
    return _replace_samples(sig, sig.samples * w)


def fft_analyze(sig: Signal) -> SpectralResult:
    """Compute FFT magnitudes, peak frequency, and a severity index."""
    n = sig.n_samples
    freqs = np.fft.rfftfreq(n, d=1.0 / sig.sample_rate)
    magnitudes = np.abs(np.fft.rfft(sig.samples)) / n
    peak_idx = int(np.argmax(magnitudes))
    severity = float(np.sqrt(np.mean(magnitudes**2)))

    return SpectralResult(
        frequencies=freqs,
        magnitudes=magnitudes,
        peak_frequency=float(freqs[peak_idx]),
        peak_magnitude=float(magnitudes[peak_idx]),
        severity_index=severity,
        timestamp=sig.timestamp,
        channel=sig.channel,
    )


def bandpass(
    low_hz: float, high_hz: float, order: int = 4
) -> Callable[[Signal], Signal]:
    """Return a band-pass transform ready for pipeline composition."""
    spec = FilterSpec(low_hz=low_hz, high_hz=high_hz, kind="bandpass", order=order)
    return partial(apply_filter, spec)


def lowpass(high_hz: float, order: int = 4) -> Callable[[Signal], Signal]:
    """Return a low-pass transform ready for pipeline composition."""
    spec = FilterSpec(low_hz=0.0, high_hz=high_hz, kind="lowpass", order=order)
    return partial(apply_filter, spec)


def windowed(name: str = "hann") -> Callable[[Signal], Signal]:
    """Return a windowing transform ready for pipeline composition."""
    return partial(apply_window_fn, name)


def _replace_samples(sig: Signal, new_samples: np.ndarray) -> Signal:
    """Return a new Signal with updated samples and preserved metadata."""
    return Signal(
        samples=new_samples.astype(np.float64),
        sample_rate=sig.sample_rate,
        timestamp=sig.timestamp,
        channel=sig.channel,
    )
