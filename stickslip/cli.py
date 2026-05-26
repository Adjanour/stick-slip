"""CLI entrypoint — wires shell to core, emits events to a caller-supplied sink."""

from __future__ import annotations

import argparse
from typing import Callable, Optional

import numpy as np

from .assessment import assess
from .buffer import make_buffer
from .history import ModulationHistory
from .pipeline import compose
from .shell import csv_chunk_stream
from .sidebands import compute_fm, detect_sidebands
from .transforms import bandpass, detrend, fft_analyze, windowed
from .types import DrillStringParams, StickSlipAssessment, StickSlipEvent


# 1000 m steel drill string: G=80 GPa, ρ=7850 kg/m³ → fm ≈ 0.5 Hz
DEFAULT_DRILL_STRING = DrillStringParams(
    shear_modulus=80e9,
    length=1000.0,
    material_density=7850.0,
)

EventSink = Callable[[StickSlipEvent], None]


def build_pipeline():
    return compose(detrend, bandpass(0.5, 8.0), windowed("hann"), fft_analyze)


def run(
    *,
    window_seconds: float,
    sample_rate: float,
    duration_seconds: float,
    channel: str,
    chunk_size: int = 10,
    sink: Optional[EventSink] = None,
) -> None:
    buffer = make_buffer(
        window_seconds=window_seconds, sample_rate=sample_rate, channel=channel
    )
    process = build_pipeline()
    history = ModulationHistory()
    fm = compute_fm(DEFAULT_DRILL_STRING)

    n_chunks = max(1, int(duration_seconds * sample_rate) // chunk_size)

    for _, (chunk, timestamp) in zip(
        range(n_chunks),
        csv_chunk_stream(sample_rate=sample_rate, chunk_size=chunk_size, data=None),
    ):
        buffer = buffer.push(new_samples=chunk, timestamp=timestamp)

        signal = buffer.to_signal()
        if signal is None:
            continue

        spectral = process(signal)
        sideband_result = detect_sidebands(spectral, fm=fm)
        history.update(sideband_result)
        assessment: StickSlipAssessment = assess(
            sideband_result=sideband_result,
            growth_rate=history.growth_rate(),
            is_growing=history.is_growing(),
        )

        # Emit event to the caller-supplied sink (console, Kafka, etc.)
        if sink is not None:
            sink(
                StickSlipEvent(
                    version="v1",
                    source="stickslip-cli",
                    timestamp=assessment.timestamp,
                    channel=assessment.channel,
                    status=assessment.status,
                    carrier_frequency=assessment.carrier_frequency,
                    modulation_frequency=assessment.modulation_frequency,
                    modulation_index=assessment.modulation_index,
                    growth_rate=assessment.growth_rate,
                    sidebands_present=assessment.sidebands_present,
                    sidebands_growing=assessment.sidebands_growing,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the stick-slip demo pipeline")
    parser.add_argument("--window-seconds", type=float, default=5.0)
    parser.add_argument("--sample-rate", type=float, default=50.0)
    parser.add_argument("--duration-seconds", type=float, default=15.0)
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--channel", default="RPM")
    args = parser.parse_args()

    def _sink(event: StickSlipEvent) -> None:
        print(
            f"[{event.channel:>6}] status={event.status} mi={event.modulation_index:.4f} "
            f"g={event.growth_rate:+.5f}/s"
        )

    run(
        window_seconds=args.window_seconds,
        sample_rate=args.sample_rate,
        duration_seconds=args.duration_seconds,
        chunk_size=args.chunk_size,
        channel=args.channel,
        sink=_sink,
    )


if __name__ == "__main__":
    main()
