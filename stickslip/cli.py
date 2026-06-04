"""
CLI entrypoint — wires shell to core, emits events to a caller-supplied sink.

Two parallel detection tracks (threaded):
  1. Sideband analysis (FFT on RPM) → StickSlipEvent
  2. Torsional energy accumulation (torque) → EnergyEvent
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Callable, Optional

import numpy as np

from .assessment import assess
from .buffer import make_buffer
from .config import Config, PipelineConfig, load_config
from .dashboard import Dashboard, run_dashboard, stop_dashboard
from .energy import EnergyHistory, OffBottomTracker, compute_energy
from .history import ModulationHistory
from .mitigation import MitigationController
from .pipeline import compose
from .shell import csv_chunk_stream
from .sidebands import compute_fm, detect_sidebands
from .transforms import bandpass, detrend, fft_analyze, windowed
from .types import (
    DrillStringParams,
    EnergyEvent,
    MitigationSignal,
    StickSlipAssessment,
    StickSlipEvent,
    TorsionalEnergyResult,
)

StickSlipSink = Callable[[StickSlipEvent], None]
EnergySink = Callable[[EnergyEvent], None]


def build_pipeline(fc: Config) -> Callable:
    """Signal processing chain: detrend → bandpass → window → FFT → SpectralResult."""
    return compose(
        detrend,
        bandpass(fc.filter.low_hz, fc.filter.high_hz, order=fc.filter.order),
        windowed("hann"),
        fft_analyze,
    )


def _sideband_track(
    *,
    cfg: Config,
    stick_slip_sink: Optional[StickSlipSink] = None,
    stop_event: Optional[Event] = None,
) -> None:
    """Thread target: read RPM chunks, buffer, FFT, detect sidebands, emit StickSlipEvents."""
    p = cfg.pipeline
    buffer = make_buffer(
        window_seconds=p.window_seconds,
        sample_rate=p.sample_rate,
        channel=p.channel,
    )
    process = build_pipeline(cfg)
    history = ModulationHistory()
    fm = compute_fm(
        DrillStringParams(
            shear_modulus=cfg.drill_string.shear_modulus,
            length=cfg.drill_string.length,
            material_density=cfg.drill_string.material_density,
        )
    )
    n_chunks = max(1, int(p.duration_seconds * p.sample_rate) // p.chunk_size)
    stream = csv_chunk_stream(
        sample_rate=p.sample_rate, chunk_size=p.chunk_size, column="bit_rpm"
    )

    sb = cfg.sideband
    for _ in range(n_chunks):
        if stop_event and stop_event.is_set():
            break
        chunk, ts = next(stream)
        buffer = buffer.push(new_samples=chunk, timestamp=ts)
        signal = buffer.to_signal()
        if signal is not None:
            spectral = process(signal)
            sideband_result = detect_sidebands(
                spectral,
                fm=fm,
                n_max=sb.max_order,
                search_window_hz=sb.search_window_hz,
                min_ratio=sb.min_ratio,
            )
            history.update(sideband_result)
            assessment: StickSlipAssessment = assess(
                sideband_result=sideband_result,
                growth_rate=history.growth_rate(),
                is_growing=history.is_growing(),
                mitigate_threshold=cfg.assessment.mitigate_threshold,
            )
            if stick_slip_sink is not None:
                stick_slip_sink(
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


def _energy_track(
    *,
    cfg: Config,
    energy_sink: Optional[EnergySink] = None,
    stop_event: Optional[Event] = None,
) -> None:
    """Thread target: read torque chunks, compute torsional energy, emit EnergyEvents."""
    p = cfg.pipeline
    buffer = make_buffer(
        window_seconds=p.window_seconds,
        sample_rate=p.sample_rate,
        channel="Torque",
    )
    energy_history = EnergyHistory()
    n_chunks = max(1, int(p.duration_seconds * p.sample_rate) // p.chunk_size)
    stream = csv_chunk_stream(
        sample_rate=p.sample_rate, chunk_size=p.chunk_size, column="torque"
    )

    prev_temp_bit: Optional[float] = None
    TEMP_SIGNIFICANT_DELTA = 5.0  # °C
    off_tracker = OffBottomTracker(initial=cfg.bha.t_off_bottom)

    for _ in range(n_chunks):
        if stop_event and stop_event.is_set():
            break
        chunk, ts = next(stream)
        buffer = buffer.push(new_samples=chunk, timestamp=ts)
        signal = buffer.to_signal()
        if signal is not None:
            mean_torque = float(np.mean(signal.samples))

            # Dynamically recapture off-bottom torque from rolling minima
            off_tracker.update_min(mean_torque)

            result = compute_energy(
                t_surface=mean_torque,
                bit_depth=p.bit_depth,
                config=cfg.bha,
                t_off_bottom=off_tracker.value,
            )
            result = TorsionalEnergyResult(
                timestamp=ts,
                t_surface=result.t_surface,
                t_bit=result.t_bit,
                k_total=result.k_total,
                theta=result.theta,
                energy=result.energy,
                bit_depth=result.bit_depth,
                temp_bit=result.temp_bit,
                g_derating_pct=result.g_derating_pct,
                t_off_bottom=off_tracker.value,
            )

            # Detect significant temperature changes (depth-driven)
            if prev_temp_bit is not None:
                delta = abs(result.temp_bit - prev_temp_bit)
                if delta >= TEMP_SIGNIFICANT_DELTA:
                    print(
                        f"[TEMP] significant change: "
                        f"{prev_temp_bit:.1f}°C → {result.temp_bit:.1f}°C "
                        f"at {p.bit_depth:.0f}m "
                        f"(Δ{delta:.1f}°C, G derating {result.g_derating_pct:.2f}%)"
                    )
            prev_temp_bit = result.temp_bit

            energy_history.update(result)
            assessment = energy_history.assess(ts)
            if energy_sink is not None:
                energy_sink(
                    EnergyEvent(
                        version="v1",
                        source="stickslip-cli",
                        timestamp=ts,
                        status=assessment.status,
                        energy=assessment.energy,
                        peak_energy=assessment.peak_energy,
                        drop_ratio=assessment.drop_ratio,
                        t_bit=result.t_bit,
                        k_total=result.k_total,
                        bit_depth=p.bit_depth,
                        temp_bit=result.temp_bit,
                        g_derating_pct=result.g_derating_pct,
                        t_off_bottom=off_tracker.value,
                    )
                )


def run(
    *,
    cfg: Config,
    stick_slip_sink: Optional[StickSlipSink] = None,
    energy_sink: Optional[EnergySink] = None,
    stop_event: Optional[Event] = None,
) -> None:
    """Run both detection tracks in parallel via ThreadPoolExecutor."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        ss = ex.submit(
            _sideband_track,
            cfg=cfg,
            stick_slip_sink=stick_slip_sink,
            stop_event=stop_event,
        )
        en = ex.submit(
            _energy_track,
            cfg=cfg,
            energy_sink=energy_sink,
            stop_event=stop_event,
        )
        for f in (ss, en):
            f.result()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the stick-slip demo pipeline")
    parser.add_argument(
        "--config", type=str, default=None, help="path to TOML config file"
    )
    parser.add_argument("--window-seconds", type=float, default=None)
    parser.add_argument("--sample-rate", type=float, default=None)
    parser.add_argument("--duration-seconds", type=float, default=None)
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--channel", default=None)
    parser.add_argument("--bit-depth", type=float, default=None)
    parser.add_argument("--baseline-rpm", type=float, default=None)
    parser.add_argument("--baseline-wob", type=float, default=None)
    parser.add_argument(
        "--dashboard", action="store_true", help="enable live rich TUI dashboard"
    )
    parser.add_argument(
        "--tui", action="store_true", help="enable interactive Textual TUI"
    )
    parser.add_argument("--web", action="store_true", help="enable FastHTML web UI")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="web UI host")
    parser.add_argument("--port", type=int, default=8080, help="web UI port")
    args = parser.parse_args()

    # Start from config file or defaults, then overlay CLI args
    cfg = load_config(args.config) if args.config else Config(dashboard=args.dashboard)

    overrides = {}
    if args.window_seconds is not None:
        overrides["window_seconds"] = args.window_seconds
    if args.sample_rate is not None:
        overrides["sample_rate"] = args.sample_rate
    if args.duration_seconds is not None:
        overrides["duration_seconds"] = args.duration_seconds
    if args.chunk_size is not None:
        overrides["chunk_size"] = args.chunk_size
    if args.channel is not None:
        overrides["channel"] = args.channel
    if args.bit_depth is not None:
        overrides["bit_depth"] = args.bit_depth
    if args.baseline_rpm is not None:
        overrides["baseline_rpm"] = args.baseline_rpm
    if args.baseline_wob is not None:
        overrides["baseline_wob"] = args.baseline_wob

    if overrides:
        p = cfg.pipeline
        cfg = Config(
            pipeline=PipelineConfig(
                window_seconds=overrides.get("window_seconds", p.window_seconds),
                sample_rate=overrides.get("sample_rate", p.sample_rate),
                chunk_size=overrides.get("chunk_size", p.chunk_size),
                channel=overrides.get("channel", p.channel),
                duration_seconds=overrides.get("duration_seconds", p.duration_seconds),
                bit_depth=overrides.get("bit_depth", p.bit_depth),
                baseline_rpm=overrides.get("baseline_rpm", p.baseline_rpm),
                baseline_wob=overrides.get("baseline_wob", p.baseline_wob),
            ),
            filter=cfg.filter,
            drill_string=cfg.drill_string,
            bha=cfg.bha,
            sideband=cfg.sideband,
            assessment=cfg.assessment,
            mitigation=cfg.mitigation,
            dashboard=args.dashboard,
        )

    # Route to the appropriate UI
    if args.tui:
        _run_tui(cfg)
    elif args.web:
        _run_web(cfg, host=args.host, port=args.port)
    else:
        _run_rich(cfg)


def _run_rich(cfg: Config) -> None:
    """Original rich-based dashboard mode."""
    dashboard = Dashboard() if cfg.dashboard else None

    def _mitigation_sink(signal: MitigationSignal) -> None:
        print(
            f"[MITIGATE] rpm={signal.rpm_setpoint:.0f} wob={signal.wob_setpoint:.0f}"
            f" — {signal.reason}"
        )

    mc = cfg.mitigation
    controller = MitigationController(
        baseline_rpm=cfg.pipeline.baseline_rpm,
        baseline_wob=cfg.pipeline.baseline_wob,
        sink=dashboard.on_mitigation if dashboard else _mitigation_sink,
        rpm_boost=mc.rpm_boost,
        wob_cut=mc.wob_cut,
        energy_wob_cut=mc.energy_wob_cut,
        ramp_step=mc.ramp_step,
        mitigate_rpm_boost=mc.mitigate_rpm_boost,
        mitigate_wob_cut=mc.mitigate_wob_cut,
        energy_rpm_boost=mc.energy_rpm_boost,
        energy_building_wob_cut=mc.energy_building_wob_cut,
    )

    def _ss_sink(event: StickSlipEvent) -> None:
        if dashboard:
            dashboard.on_stick_slip(event)
        else:
            print(
                f"[{event.channel:>6}] sb={event.status} mi={event.modulation_index:.4f} "
                f"g={event.growth_rate:+.5f}/s"
            )
        controller.on_stick_slip(event)

    def _energy_sink(event: EnergyEvent) -> None:
        if dashboard:
            dashboard.on_energy(event)
        else:
            print(
                f"[ENERGY] status={event.status} U={event.energy:.2f}J "
                f"peak={event.peak_energy:.2f}J drop={event.drop_ratio:.2%}"
            )
        controller.on_energy(event)

    live = run_dashboard(dashboard) if dashboard else None
    try:
        run(cfg=cfg, stick_slip_sink=_ss_sink, energy_sink=_energy_sink)
    finally:
        if live:
            stop_dashboard(live)


def _run_tui(cfg: Config) -> None:
    from .tui import run_tui

    run_tui(cfg)


def _run_web(cfg: Config, host: str, port: int) -> None:
    from .webui import run_web

    run_web(cfg, host=host, port=port)


if __name__ == "__main__":
    main()
