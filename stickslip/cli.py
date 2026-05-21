"""CLI entrypoint for the stick-slip demo pipeline."""

from __future__ import annotations

import argparse

import numpy as np

from .buffer import make_buffer
from .pipeline import compose
from .shell import render_display, sensor_stream, throttled_display
from .transforms import bandpass, detrend, fft_analyze, windowed


def build_pipeline():
    return compose(detrend, bandpass(0.5, 8.0), windowed("hann"), fft_analyze)


def run(
    *,
    window_seconds: float,
    sample_rate: float,
    duration_seconds: float,
    channels: tuple[str, ...],
) -> None:
    buffers = {
        channel: make_buffer(
            window_seconds=window_seconds, sample_rate=sample_rate, channel=channel
        )
        for channel in channels
    }
    process = build_pipeline()
    maybe_emit = throttled_display(1.0)

    max_ticks = max(1, int(duration_seconds * sample_rate))

    for _, (readings, timestamp) in zip(
        range(max_ticks), sensor_stream(sample_rate=sample_rate, channels=channels)
    ):
        for channel, value in readings.items():
            buffers[channel] = buffers[channel].push(
                new_samples=np.array([value], dtype=np.float64),
                timestamp=timestamp,
            )

            signal = buffers[channel].to_signal()
            if signal is None:
                continue

            result = process(signal)
            update = maybe_emit(result)
            if update is not None:
                render_display(update)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the stick-slip demo pipeline")
    parser.add_argument("--window-seconds", type=float, default=5.0)
    parser.add_argument("--sample-rate", type=float, default=50.0)
    parser.add_argument("--duration-seconds", type=float, default=15.0)
    parser.add_argument("--channels", nargs="*", default=["RPM", "Torque"])
    args = parser.parse_args()

    run(
        window_seconds=args.window_seconds,
        sample_rate=args.sample_rate,
        duration_seconds=args.duration_seconds,
        channels=tuple(args.channels),
    )


if __name__ == "__main__":
    main()
