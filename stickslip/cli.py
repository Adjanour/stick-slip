"""
CLI entrypoint — wires shell to core, emits events to a caller-supplied sink.

Two parallel detection tracks (threaded):
  1. Sideband analysis (FFT on RPM) → StickSlipEvent
  2. Torsional energy accumulation (torque) → EnergyEvent

Thread safety: a threading.Barrier keeps both tracks on the same CSV/sensor
chunk at the same wall-clock time.  Exceptions in either track abort the
barrier so the other track does not hang forever.
"""

from __future__ import annotations

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any, Callable, Optional

import numpy as np

from .assessment import assess
from .buffer import make_buffer
from .campbell import CampbellCollector, CampbellPoint
from .config import Config, PipelineConfig, load_config
from .dashboard import Dashboard, run_dashboard, stop_dashboard
from .energy import EnergyHistory, OffBottomTracker, compute_energy
from .history import ModulationHistory
from .mitigation import MitigationController
from .pipeline import compose
from .shell import SharedCsvSource
from .sidebands import compute_fm, detect_sidebands
from .ssi import compute_ssi
from .transforms import bandpass, detrend, fft_analyze, windowed
from .types import (
    DrillStringParams,
    EnergyEvent,
    MINIMAL,
    MitigationSignal,
    StickSlipAssessment,
    StickSlipEvent,
    TorsionalEnergyResult,
)

StickSlipSink = Callable[[StickSlipEvent], None]
EnergySink = Callable[[EnergyEvent], None]
PausedCallback = Callable[[bool], None]


def build_pipeline(fc: Config) -> Callable:
    """Signal processing chain: detrend → bandpass → window → FFT → SpectralResult."""
    return compose(
        detrend,
        bandpass(fc.filter.low_hz, fc.filter.high_hz, order=fc.filter.order),
        windowed("hann"),
        fft_analyze,
    )


# ---------------------------------------------------------------------------
# NaN / inf guard — replace non-finite values with 0 to prevent silent
# propagation through FFT / statistics.
# ---------------------------------------------------------------------------

_BAD_VALUE = 0.0


def _guard_chunk(chunk: np.ndarray) -> np.ndarray:
    mask = ~np.isfinite(chunk)
    if np.any(mask):
        chunk = chunk.copy()
        chunk[mask] = _BAD_VALUE
    return chunk


def _guard_signal_samples(signal: Any) -> Any:
    """Guard Signal.samples in-place (called after push, before FFT)."""
    mask = ~np.isfinite(signal.samples)
    if np.any(mask):
        signal.samples = signal.samples.copy()
        signal.samples[mask] = _BAD_VALUE
    return signal


# ---------------------------------------------------------------------------
# Shared barrier timeout constant (seconds)
#   If a barrier.wait() exceeds this, we assume the other thread is dead
#   and abort.  With a 5-second FFT window and 50 Hz sampling, each chunk
#   takes ~0.2 s of wall time, so 10x margin (2 s) is generous.
# ---------------------------------------------------------------------------

_BARRIER_TIMEOUT = 30.0


def _safe_barrier_wait(barrier: Optional[threading.Barrier]) -> None:
    """Wait on the barrier with a timeout; abort on timeout so no deadlock."""
    if barrier is None:
        return
    try:
        barrier.wait(timeout=_BARRIER_TIMEOUT)
    except threading.BrokenBarrierError:
        pass  # peer already aborted — exit gracefully
    except threading.BarrierError:
        try:
            barrier.abort()
        except threading.BrokenBarrierError:
            pass


# ---------------------------------------------------------------------------
# Detection tracks
# ---------------------------------------------------------------------------


def _sideband_track(
    *,
    cfg: Config,
    stick_slip_sink: Optional[StickSlipSink] = None,
    stop_event: Optional[Event] = None,
    campbell_collector: Optional[CampbellCollector] = None,
    paused_callback: Optional[PausedCallback] = None,
    source: Optional[Any] = None,
    barrier: Optional[threading.Barrier] = None,
    n_chunks: Optional[int] = None,
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
    if n_chunks is None:
        n_chunks = max(1, int(p.duration_seconds * p.sample_rate) // p.chunk_size)

    sb = cfg.sideband
    ac = cfg.assessment
    zero_since: Optional[float] = None
    last_paused_state = False
    prev_status: str = MINIMAL
    rpm_zero_threshold = p.rpm_zero_threshold
    paused_duration = p.paused_duration

    barrier_exc: Optional[BaseException] = None

    for _ in range(n_chunks):
        if stop_event and stop_event.is_set():
            break
        try:
            chunk = _guard_chunk(source.next_rpm())
            ts = time.time()
            buffer.push(new_samples=chunk, timestamp=ts)
            signal = buffer.to_signal()

            # Detect drilling paused (RPM near zero for paused_duration seconds)
            chunk_mean = float(np.mean(chunk))
            if chunk_mean < rpm_zero_threshold:
                if zero_since is None:
                    zero_since = ts
                elif ts - zero_since >= paused_duration and not last_paused_state:
                    last_paused_state = True
                    if paused_callback is not None:
                        paused_callback(True)
            else:
                if last_paused_state:
                    last_paused_state = False
                    if paused_callback is not None:
                        paused_callback(False)
                zero_since = None

            if signal is not None:
                _guard_signal_samples(signal)
                spectral = process(signal)
                sideband_result = detect_sidebands(
                    spectral,
                    fm=fm,
                    n_max=sb.max_order,
                    search_window_hz=sb.search_window_hz,
                    min_ratio=sb.min_ratio,
                    min_carrier_magnitude=sb.min_carrier_magnitude,
                    carrier_magnitude_relative=sb.carrier_magnitude_relative,
                )
                history.update(sideband_result)
                assessment: StickSlipAssessment = assess(
                    sideband_result=sideband_result,
                    growth_rate=history.growth_rate(),
                    is_growing=history.is_growing(),
                    mitigate_threshold=ac.mitigate_threshold,
                    absolute_mitigate_mi=ac.absolute_mitigate_mi,
                    prev_status=prev_status,
                    hysteresis_release_mi=ac.hysteresis_release_mi,
                    hysteresis_release_rate=ac.hysteresis_release_rate,
                )
                prev_status = assessment.status
                if campbell_collector is not None:
                    rpm_pt = assessment.carrier_frequency * 60.0
                    ssi_val = compute_ssi(assessment.modulation_index)
                    campbell_collector.add(
                        CampbellPoint(
                            rpm=rpm_pt,
                            fm=assessment.modulation_frequency,
                            ssi=ssi_val,
                            timestamp=assessment.timestamp,
                            status=assessment.status,
                        )
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

            _safe_barrier_wait(barrier)

        except BaseException as exc:
            barrier_exc = exc
            if barrier is not None:
                try:
                    barrier.abort()
                except threading.BrokenBarrierError:
                    pass
            break

    if barrier_exc is not None and not isinstance(barrier_exc, SystemExit):
        raise barrier_exc  # type: ignore[misc]


def _energy_track(
    *,
    cfg: Config,
    energy_sink: Optional[EnergySink] = None,
    stop_event: Optional[Event] = None,
    source: Optional[Any] = None,
    barrier: Optional[threading.Barrier] = None,
    n_chunks: Optional[int] = None,
) -> None:
    """Thread target: read torque chunks, compute torsional energy, emit EnergyEvents."""
    p = cfg.pipeline
    buffer = make_buffer(
        window_seconds=p.window_seconds,
        sample_rate=p.sample_rate,
        channel="Torque",
    )
    energy_history = EnergyHistory()
    if n_chunks is None:
        n_chunks = max(1, int(p.duration_seconds * p.sample_rate) // p.chunk_size)

    prev_temp_bit: Optional[float] = None
    TEMP_SIGNIFICANT_DELTA = 5.0  # °C
    off_tracker = OffBottomTracker(initial=cfg.bha.t_off_bottom)

    barrier_exc: Optional[BaseException] = None

    for _ in range(n_chunks):
        if stop_event and stop_event.is_set():
            break
        try:
            chunk = _guard_chunk(source.next_torque())
            ts = time.time()
            buffer.push(new_samples=chunk, timestamp=ts)
            signal = buffer.to_signal()
            if signal is not None:
                _guard_signal_samples(signal)
                mean_torque = float(np.mean(signal.samples))

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

            _safe_barrier_wait(barrier)

        except BaseException as exc:
            barrier_exc = exc
            if barrier is not None:
                try:
                    barrier.abort()
                except threading.BrokenBarrierError:
                    pass
            break

    if barrier_exc is not None and not isinstance(barrier_exc, SystemExit):
        raise barrier_exc  # type: ignore[misc]


def run(
    *,
    cfg: Config,
    stick_slip_sink: Optional[StickSlipSink] = None,
    energy_sink: Optional[EnergySink] = None,
    stop_event: Optional[Event] = None,
    campbell_collector: Optional[CampbellCollector] = None,
    paused_callback: Optional[PausedCallback] = None,
) -> None:
    """Run both detection tracks in parallel via ThreadPoolExecutor."""
    cfg.validate()

    source = SharedCsvSource(
        chunk_size=cfg.pipeline.chunk_size, sample_rate=cfg.pipeline.sample_rate
    )
    n_chunks = max(1, source.total_values // cfg.pipeline.chunk_size)
    barrier: threading.Barrier = threading.Barrier(2, action=source.advance)

    with ThreadPoolExecutor(max_workers=2) as ex:
        ss = ex.submit(
            _sideband_track,
            cfg=cfg,
            stick_slip_sink=stick_slip_sink,
            stop_event=stop_event,
            campbell_collector=campbell_collector,
            paused_callback=paused_callback,
            source=source,
            barrier=barrier,
            n_chunks=n_chunks,
        )
        en = ex.submit(
            _energy_track,
            cfg=cfg,
            energy_sink=energy_sink,
            stop_event=stop_event,
            source=source,
            barrier=barrier,
            n_chunks=n_chunks,
        )
        exceptions: list[BaseException] = []
        for f in (ss, en):
            try:
                f.result()
            except BaseException as exc:
                exceptions.append(exc)
        if exceptions:
            msg = "; ".join(str(e) for e in exceptions)
            raise RuntimeError(f"Pipeline tracks failed: {msg}")


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
    parser.add_argument(
        "--dwis-endpoint", type=str, default=None,
        help="OPC-UA endpoint for D‑WIS live data (default: csv replay)",
    )
    args = parser.parse_args()

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
            dwis=cfg.dwis,
            dashboard=args.dashboard,
        )

    # D‑WIS endpoint overrides config
    if args.dwis_endpoint is not None:
        dwis = cfg.dwis
        cfg = Config(
            pipeline=cfg.pipeline,
            filter=cfg.filter,
            drill_string=cfg.drill_string,
            bha=cfg.bha,
            sideband=cfg.sideband,
            assessment=cfg.assessment,
            mitigation=cfg.mitigation,
            dwis=DwisConfig(
                endpoint=args.dwis_endpoint,
                username=dwis.username,
                password=dwis.password,
                reconnect_attempts=dwis.reconnect_attempts,
                reconnect_delay_s=dwis.reconnect_delay_s,
            ),
            dashboard=args.dashboard,
        )

    cfg.validate()

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
        run(
            cfg=cfg,
            stick_slip_sink=_ss_sink,
            energy_sink=_energy_sink,
            paused_callback=dashboard.on_paused if dashboard else None,
        )
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
