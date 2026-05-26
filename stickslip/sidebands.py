"""
Sideband detection — pure functions.

Stick-slip modulates the RPM carrier at the torsional frequency fm,
creating sideband peaks at fc ± n·fm.  The modulation index (MI) is the
strongest sideband-to-carrier ratio — it quantifies torsional vibration
intensity.
"""

from __future__ import annotations

import numpy as np

from .types import DrillStringParams, SidebandResult, SpectralResult


def detect_sidebands(
    spectral: SpectralResult,
    fm: float,
    n_max: int = 3,
    search_window_hz: float = 0.15,
    min_ratio: float = 0.05,
) -> SidebandResult:
    freqs = spectral.frequencies
    mags = spectral.magnitudes
    fc = spectral.peak_frequency
    mc = spectral.peak_magnitude

    if mc < 1e-12:
        return _empty(fc, mc, fm, spectral)

    orders = np.arange(1, n_max + 1, dtype=np.int32)
    upper_expected = fc + orders * fm
    lower_expected = fc - orders * fm
    valid_lower = lower_expected > 0.0

    # Build sideband data as lists, then convert to parallel arrays at return
    sb_orders: list[int] = []
    sb_is_upper: list[bool] = []
    sb_expected_hz: list[float] = []
    sb_actual_hz: list[float] = []
    sb_magnitudes: list[float] = []
    sb_ratios: list[float] = []

    for i, n in enumerate(orders):
        _search(
            freqs,
            mags,
            mc,
            upper_expected[i],
            int(n),
            True,
            search_window_hz,
            min_ratio,
            sb_orders,
            sb_is_upper,
            sb_expected_hz,
            sb_actual_hz,
            sb_magnitudes,
            sb_ratios,
        )
        if valid_lower[i]:
            _search(
                freqs,
                mags,
                mc,
                lower_expected[i],
                int(n),
                False,
                search_window_hz,
                min_ratio,
                sb_orders,
                sb_is_upper,
                sb_expected_hz,
                sb_actual_hz,
                sb_magnitudes,
                sb_ratios,
            )

    mi = float(max(sb_ratios)) if sb_ratios else 0.0
    n = len(sb_orders)

    def _arr(v, dtype):
        return np.array(v, dtype=dtype) if n else np.array([], dtype=dtype)

    return SidebandResult(
        carrier_frequency=fc,
        carrier_magnitude=mc,
        modulation_frequency=fm,
        modulation_index=mi,
        sidebands_present=n > 0,
        timestamp=spectral.timestamp,
        channel=spectral.channel,
        sb_orders=_arr(sb_orders, np.int32),
        sb_is_upper=_arr(sb_is_upper, bool),
        sb_expected_hz=_arr(sb_expected_hz, np.float64),
        sb_actual_hz=_arr(sb_actual_hz, np.float64),
        sb_magnitudes=_arr(sb_magnitudes, np.float64),
        sb_ratios=_arr(sb_ratios, np.float64),
    )


# Search ±search_window_hz around the expected frequency for the highest peak
def _search(
    freqs: np.ndarray,
    mags: np.ndarray,
    mc: float,
    expected_hz: float,
    order: int,
    is_upper: bool,
    search_window_hz: float,
    min_ratio: float,
    sb_orders: list[int],
    sb_is_upper: list[bool],
    sb_expected_hz: list[float],
    sb_actual_hz: list[float],
    sb_magnitudes: list[float],
    sb_ratios: list[float],
) -> None:
    lo = int(np.searchsorted(freqs, expected_hz - search_window_hz))
    hi = int(np.searchsorted(freqs, expected_hz + search_window_hz))
    if hi <= lo:
        return

    window_mags = mags[lo:hi]
    local_idx = int(np.argmax(window_mags))
    peak_mag = float(window_mags[local_idx])
    peak_freq = float(freqs[lo + local_idx])
    ratio = peak_mag / mc
    if ratio < min_ratio:
        return

    sb_orders.append(order)
    sb_is_upper.append(is_upper)
    sb_expected_hz.append(expected_hz)
    sb_actual_hz.append(peak_freq)
    sb_magnitudes.append(peak_mag)
    sb_ratios.append(ratio)


def compute_fm(params: DrillStringParams) -> float:
    return params.modulation_frequency()


def _empty(fc: float, mc: float, fm: float, spectral: SpectralResult) -> SidebandResult:
    z = np.array([], dtype=np.float64)
    return SidebandResult(
        carrier_frequency=fc,
        carrier_magnitude=mc,
        modulation_frequency=fm,
        modulation_index=0.0,
        sidebands_present=False,
        timestamp=spectral.timestamp,
        channel=spectral.channel,
        sb_orders=np.array([], dtype=np.int32),
        sb_is_upper=np.array([], dtype=bool),
        sb_expected_hz=z,
        sb_actual_hz=z,
        sb_magnitudes=z,
        sb_ratios=z,
    )
