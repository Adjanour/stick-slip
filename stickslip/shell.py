"""
Effectful shell - the only place I/O lives.
"""

from __future__ import annotations

import time
from typing import Callable, Generator, Optional

import numpy as np

from .types import DisplayUpdate, SpectralResult


SensorReader = Callable[[], float]
ChannelReadings = dict[str, float]


def simulate_signal(
    base: float = 120.0,
    stick_slip_hz: float = 0.5,
    stick_slip_amplitude: float = 30.0,
    noise_std: float = 2.0,
) -> SensorReader:
    """Return a synthetic scalar sensor source for one drilling channel."""

    def _read() -> float:
        t = time.time()
        return (
            base
            + stick_slip_amplitude * np.sin(2 * np.pi * stick_slip_hz * t)
            + np.random.normal(0.0, noise_std)
        )

    return _read


def simulate_channel_readers() -> dict[str, SensorReader]:
    """Return independent RPM and Torque sources for acquisition demos."""
    return {
        "RPM": simulate_signal(
            base=120.0, stick_slip_hz=0.5, stick_slip_amplitude=30.0, noise_std=2.0
        ),
        "Torque": simulate_signal(
            base=1800.0, stick_slip_hz=0.5, stick_slip_amplitude=120.0, noise_std=8.0
        ),
    }


def sensor_stream(
    sample_rate: float = 50.0,
    channels: tuple[str, ...] = ("RPM", "Torque"),
    sources: Optional[dict[str, SensorReader]] = None,
) -> Generator[tuple[ChannelReadings, float], None, None]:
    """Yield per-channel scalar readings at the requested sample rate."""
    readers = sources or simulate_channel_readers()
    dt = 1.0 / sample_rate

    while True:
        t0 = time.time()
        readings = {channel: float(readers[channel]()) for channel in channels}
        yield readings, t0

        elapsed = time.time() - t0
        sleep_time = dt - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)


def throttled_display(
    update_rate_hz: float = 1.0,
) -> Callable[[SpectralResult], Optional[DisplayUpdate]]:
    """Return a display gate that emits at most once per channel per interval."""

    last_emit: dict[str, float] = {}
    min_interval = 1.0 / update_rate_hz

    def _maybe_emit(result: SpectralResult) -> Optional[DisplayUpdate]:
        now = result.timestamp
        previous = last_emit.get(result.channel, 0.0)
        if now - previous >= min_interval:
            last_emit[result.channel] = now
            return DisplayUpdate(
                timestamp=now,
                channel=result.channel,
                peak_frequency=result.peak_frequency,
                severity_index=result.severity_index,
            )
        return None

    return _maybe_emit


def render_display(update: DisplayUpdate) -> None:
    """Render one channel update to stdout."""
    bar_len = 30
    normalised = min(update.severity_index * 500, 1.0)
    bar = "█" * int(normalised * bar_len) + "░" * (bar_len - int(normalised * bar_len))

    print(
        f"[{update.channel:>6}]  "
        f"t={update.timestamp:>10.3f}s  │  "
        f"Peak: {update.peak_frequency:>6.3f} Hz  │  "
        f"Severity: {update.severity_index:.5f}  │  "
        f"[{bar}]"
    )
