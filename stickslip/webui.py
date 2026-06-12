"""
FastHTML web UI — real-time monitoring dashboard with HTMX polling.

Run via: stickslip --web
"""

from __future__ import annotations

import base64
import threading
import time
from collections import deque
from typing import Optional

import uvicorn

from fasthtml.common import (
    A,
    Button,
    Details,
    Div,
    FastHTML,
    Form,
    H1,
    H3,
    Header,
    Input,
    Link,
    Meta,
    Response,
    Span,
    Summary,
    Table,
    Td,
    Th,
    Title,
    Tr,
)

from .campbell import CampbellCollector, render_campbell_diagram
from .cli import run as run_pipeline
from .config import (
    AssessmentConfig,
    Config,
    FilterConfig,
    MitigationConfig,
    PipelineConfig,
    SidebandConfig,
)
from .mitigation import MitigationController
from .report import generate_report
from .ssi import (
    SSI_CRITICAL,
    SSI_MILD,
    SSI_MODERATE,
    SSI_NONE,
    SSI_SEVERE,
    SSI_STYLE,
    compute_ssi,
    ssi_class,
    ssi_description,
)
from .types import (
    ENERGY_BUILDING,
    ENERGY_NORMAL,
    ENERGY_RELEASE,
    EnergyEvent,
    INTENSIFYING,
    MINIMAL,
    MITIGATE,
    MitigationSignal,
    STABLE,
    StickSlipEvent,
)

# ---------------------------------------------------------------------------
# Shared state — updated by pipeline thread, read by web handlers
# ---------------------------------------------------------------------------


class SharedState:
    """Thread-safe shared state between pipeline thread and web handlers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ss: Optional[StickSlipEvent] = None
        self._energy: Optional[EnergyEvent] = None
        self._mitigation: Optional[MitigationSignal] = None
        self._events: deque = deque(maxlen=50)
        self._running = False
        self._last_update = 0.0
        self._drilling_paused = False
        self._all_ss_events: deque[StickSlipEvent] = deque(maxlen=10000)
        self._all_energy_events: deque[EnergyEvent] = deque(maxlen=10000)

    def update_ss(self, event: StickSlipEvent) -> None:
        with self._lock:
            self._ss = event
            ssi_val = compute_ssi(event.modulation_index)
            self._events.append(
                (event.timestamp, "SB", f"{event.status}  SSI={ssi_val:.1f}%")
            )
            self._all_ss_events.append(event)
            self._last_update = time.time()

    def update_energy(self, event: EnergyEvent) -> None:
        with self._lock:
            self._energy = event
            self._events.append(
                (event.timestamp, "EN", f"{event.status}  U={event.energy:.0f}J")
            )
            self._all_energy_events.append(event)
            self._last_update = time.time()

    def update_mitigation(self, signal: MitigationSignal) -> None:
        with self._lock:
            self._mitigation = signal
            self._events.append((signal.timestamp, "CTRL", signal.reason[:55]))
            self._last_update = time.time()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._drilling_paused = paused

    def clear(self) -> None:
        with self._lock:
            self._ss = None
            self._energy = None
            self._mitigation = None
            self._events.clear()
            self._drilling_paused = False
            self._all_ss_events.clear()
            self._all_energy_events.clear()

    def snapshot(self) -> tuple:
        with self._lock:
            return (
                self._ss,
                self._energy,
                self._mitigation,
                list(self._events),
                self._drilling_paused,
            )

    def all_events(self) -> tuple[list[StickSlipEvent], list[EnergyEvent]]:
        with self._lock:
            return list(self._all_ss_events), list(self._all_energy_events)

    def clear_full(self) -> None:
        with self._lock:
            self._all_ss_events.clear()
            self._all_energy_events.clear()

    @property
    def running(self) -> bool:
        return self._running

    @running.setter
    def running(self, value: bool) -> None:
        self._running = value


state = SharedState()
campbell_collector = CampbellCollector()

# ---------------------------------------------------------------------------
# Status style helpers
# ---------------------------------------------------------------------------

_STYLES = {
    MINIMAL: "color: green; font-weight: bold",
    STABLE: "color: cyan; font-weight: bold",
    INTENSIFYING: "color: orange; font-weight: bold",
    MITIGATE: "color: red; font-weight: bold",
    ENERGY_NORMAL: "color: green; font-weight: bold",
    ENERGY_BUILDING: "color: orange; font-weight: bold",
    ENERGY_RELEASE: "color: red; font-weight: bold",
}

_SSI_COLORS = {
    SSI_NONE: "#4caf50",
    SSI_MILD: "#00bcd4",
    SSI_MODERATE: "#ff9800",
    SSI_SEVERE: "#ff5722",
    SSI_CRITICAL: "#f44336",
}


def _status_span(status: str) -> Span:
    style = _STYLES.get(status, "")
    return Span(status, style=style)


# ---------------------------------------------------------------------------
# Panel renderers (pure HTML)
# ---------------------------------------------------------------------------


def _sideband_panel(ss: Optional[StickSlipEvent]) -> Div:
    rows = []
    if ss is not None:
        rows = [
            Tr(Td("Status:"), Td(_status_span(ss.status))),
            Tr(Td("MI:"), Td(f"{ss.modulation_index:.4f}")),
            Tr(Td("dMI/dt:"), Td(f"{ss.growth_rate:+.5f}/s")),
            Tr(Td("Carrier:"), Td(f"{ss.carrier_frequency:.2f} Hz")),
            Tr(Td("FM:"), Td(f"{ss.modulation_frequency:.2f} Hz")),
            Tr(Td("Sidebands:"), Td("Yes" if ss.sidebands_present else "No")),
        ]
    else:
        rows = [Tr(Td("Status:"), Td("waiting for data…", style="color: gray"))]

    return Div(
        H3("RPM Sideband", style="margin: 0 0 0.5rem 0"),
        Table(*rows, style="width: 100%"),
        style="border: 1px solid #4a9eff; border-radius: 6px; padding: 1rem; flex: 1; min-width: 200px",
        id="sideband-panel",
    )


def _energy_panel(en: Optional[EnergyEvent]) -> Div:
    rows = []
    if en is not None:
        rows = [
            Tr(Td("Status:"), Td(_status_span(en.status))),
            Tr(Td("Energy:"), Td(f"{en.energy:.1f} J")),
            Tr(Td("Peak:"), Td(f"{en.peak_energy:.1f} J")),
            Tr(Td("Drop:"), Td(f"{en.drop_ratio:.1%}")),
            Tr(Td("T_bit:"), Td(f"{en.t_bit:.0f} Nm")),
            Tr(Td("K_total:"), Td(f"{en.k_total:.0f} Nm/rad")),
            Tr(Td("Temp:"), Td(f"{en.temp_bit:.1f}°C at {en.bit_depth:.0f}m")),
            Tr(Td("G derate:"), Td(f"{en.g_derating_pct:.2f}%")),
            Tr(Td("T_off-bot:"), Td(f"{en.t_off_bottom:.0f} Nm")),
        ]
    else:
        rows = [Tr(Td("Status:"), Td("waiting for data…", style="color: gray"))]

    return Div(
        H3("Torsional Energy", style="margin: 0 0 0.5rem 0"),
        Table(*rows, style="width: 100%"),
        style="border: 1px solid #ffa500; border-radius: 6px; padding: 1rem; flex: 1; min-width: 200px",
        id="energy-panel",
    )


def _ssi_panel(ss: Optional[StickSlipEvent]) -> Div:
    ssi_val = compute_ssi(ss.modulation_index) if ss else 0.0
    cls = ssi_class(ssi_val) if ss else SSI_NONE
    color = _SSI_COLORS.get(cls, "#666")
    rows = []
    if ss is not None:
        rows = [
            Tr(Td("SSI:"), Td(f"{ssi_val:.2f}%", style=f"font-weight: bold; color: {color}; font-size: 1.2em")),
            Tr(Td("Class:"), Td(cls, style=f"font-weight: bold; color: {color}")),
            Tr(Td("Description:"), Td(ssi_description(ssi_val), style="color: #666; font-size: 0.9em")),
        ]
    else:
        rows = [
            Tr(Td("SSI:"), Td("—", style="color: gray")),
            Tr(Td("Class:"), Td("awaiting data", style="color: gray")),
        ]

    return Div(
        H3("Stick-Slip Severity Index", style="margin: 0 0 0.5rem 0"),
        Table(*rows, style="width: 100%"),
        style=f"border: 2px solid {color}; border-radius: 6px; padding: 1rem; flex: 1; min-width: 200px",
        id="ssi-panel",
    )


def _events_panel(events: list) -> Div:
    rows = []
    for ts, kind, msg in events:
        label = kind
        rows.append(
            Tr(
                Td(f"[{ts:7.1f}s]", style="color: gray; font-family: monospace"),
                Td(label, style="font-weight: bold"),
                Td(msg, style="font-family: monospace"),
            )
        )

    if not rows:
        rows = [
            Tr(Td("no events yet", style="color: gray; text-align: center", colspan="3"))
        ]

    return Div(
        H3("Events", style="margin: 0 0 0.5rem 0"),
        Table(
            Tr(Th("Timestamp"), Th("Type"), Th("Message"), style="text-align: left"),
            *rows,
            style="width: 100%; font-size: 0.9em",
        ),
        style="border: 1px solid #666; border-radius: 6px; padding: 1rem; margin-top: 1rem; max-height: 300px; overflow-y: auto",
        id="events-panel",
    )


# ---------------------------------------------------------------------------
# FastHTML app
# ---------------------------------------------------------------------------

app = FastHTML()

_BLINK_CSS = """
@keyframes blink-red {
  0% { background-color: #d32f2f; color: white; }
  50% { background-color: #ffffff; color: #d32f2f; }
  100% { background-color: #d32f2f; color: white; }
}
.blink-alert {
  animation: blink-red 1s infinite;
  text-align: center;
  padding: 0.5rem;
  font-weight: bold;
  border-radius: 4px;
}
.drilling-paused {
  background: #d32f2f;
  color: white;
  text-align: center;
  padding: 0.25rem;
  font-weight: bold;
  border-radius: 4px;
}
"""


@app.get("/config-form")
def config_form():
    cfg = _config
    if cfg is None:
        return Div("No config loaded", id="config-area")
    p = cfg.pipeline
    f = cfg.filter
    s = cfg.sideband
    a = cfg.assessment
    m = cfg.mitigation
    return Div(
        Form(
            H3("Pipeline", style="margin: 0.5rem 0"),
            Div(
                _field("duration_seconds", p.duration_seconds, "Simulation duration (s)"),
                _field("window_seconds", p.window_seconds, "FFT window (s)"),
                _field("sample_rate", p.sample_rate, "Sample rate (Hz)"),
                _field("chunk_size", p.chunk_size, "Chunk size (samples)"),
                _field("baseline_rpm", p.baseline_rpm, "Surface RPM setpoint"),
                _field("baseline_wob", p.baseline_wob, "WOB setpoint (N)"),
                _field("bit_depth", p.bit_depth, "Bit depth (m)"),
                style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.5rem",
            ),
            H3("Detection", style="margin: 0.5rem 0"),
            Div(
                _field("filter_low_hz", f.low_hz, "Bandpass low (Hz)"),
                _field("filter_high_hz", f.high_hz, "Bandpass high (Hz)"),
                _field("filter_order", f.order, "Filter order"),
                _field("min_ratio", s.min_ratio, "Sideband min ratio"),
                _field("search_window_hz", s.search_window_hz, "Search window (Hz)"),
                _field("max_order", s.max_order, "Max sideband order"),
                style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.5rem",
            ),
            H3("Assessment", style="margin: 0.5rem 0"),
            Div(
                _field("growing_threshold", a.growing_threshold, "Growth threshold"),
                _field("mitigate_threshold", a.mitigate_threshold, "dMI/dt mitigate threshold"),
                _field("absolute_mitigate_mi", a.absolute_mitigate_mi, "Absolute MI threshold"),
                _field("hysteresis_release_mi", a.hysteresis_release_mi, "Hysteresis release MI"),
                _field("hysteresis_release_rate", a.hysteresis_release_rate, "Hysteresis release rate"),
                style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.5rem",
            ),
            H3("Mitigation", style="margin: 0.5rem 0"),
            Div(
                _field("rpm_boost", m.rpm_boost, "RPM boost factor"),
                _field("wob_cut", m.wob_cut, "WOB cut factor"),
                _field("energy_wob_cut", m.energy_wob_cut, "Energy WOB cut"),
                _field("ramp_step", m.ramp_step, "Ramp step"),
                style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.5rem",
            ),
            Button("Save Config", type="submit", style="margin-top: 1rem; background: #1976d2; color: white; border: none; padding: 0.5rem 1.5rem; border-radius: 4px; cursor: pointer"),
            hx_post="/update-config",
            hx_target="#config-feedback",
            hx_swap="innerHTML",
        ),
        Div(id="config-feedback", style="margin-top: 0.5rem"),
        id="config-area",
    )


def _field(name: str, value: object, label: str) -> Div:
    return Div(
        Div(label, style="font-size: 0.8em; color: #666; margin-bottom: 2px"),
        Input(name=name, value=str(value), type="text",
              style="width: 100%; box-sizing: border-box; padding: 4px 6px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.9em"),
        style="margin-bottom: 0.3rem",
    )


@app.post("/update-config")
def update_config(
    duration_seconds: str = "",
    window_seconds: str = "",
    sample_rate: str = "",
    chunk_size: str = "",
    baseline_rpm: str = "",
    baseline_wob: str = "",
    bit_depth: str = "",
    filter_low_hz: str = "",
    filter_high_hz: str = "",
    filter_order: str = "",
    min_ratio: str = "",
    search_window_hz: str = "",
    max_order: str = "",
    growing_threshold: str = "",
    mitigate_threshold: str = "",
    absolute_mitigate_mi: str = "",
    hysteresis_release_mi: str = "",
    hysteresis_release_rate: str = "",
    rpm_boost: str = "",
    wob_cut: str = "",
    energy_wob_cut: str = "",
    ramp_step: str = "",
):
    global _config
    if _config is None:
        return Div("No config loaded", style="color: red")
    cfg = _config
    p = cfg.pipeline
    f = cfg.filter
    s = cfg.sideband
    a = cfg.assessment
    m = cfg.mitigation

    def _f(v: str, default: float) -> float:
        try:
            return float(v) if v else default
        except ValueError:
            return default

    def _i(v: str, default: int) -> int:
        try:
            return int(v) if v else default
        except ValueError:
            return default

    _config = Config(
        pipeline=PipelineConfig(
            window_seconds=_f(window_seconds, p.window_seconds),
            sample_rate=_f(sample_rate, p.sample_rate),
            chunk_size=_i(chunk_size, p.chunk_size),
            channel=p.channel,
            duration_seconds=_f(duration_seconds, p.duration_seconds),
            bit_depth=_f(bit_depth, p.bit_depth),
            baseline_rpm=_f(baseline_rpm, p.baseline_rpm),
            baseline_wob=_f(baseline_wob, p.baseline_wob),
        ),
        filter=FilterConfig(
            low_hz=_f(filter_low_hz, f.low_hz),
            high_hz=_f(filter_high_hz, f.high_hz),
            order=_i(filter_order, f.order),
        ),
        drill_string=cfg.drill_string,
        bha=cfg.bha,
        sideband=SidebandConfig(
            max_order=_i(max_order, s.max_order),
            min_ratio=_f(min_ratio, s.min_ratio),
            search_window_hz=_f(search_window_hz, s.search_window_hz),
        ),
        assessment=AssessmentConfig(
            growing_threshold=_f(growing_threshold, a.growing_threshold),
            mitigate_threshold=_f(mitigate_threshold, a.mitigate_threshold),
            absolute_mitigate_mi=_f(absolute_mitigate_mi, a.absolute_mitigate_mi),
            hysteresis_release_mi=_f(hysteresis_release_mi, a.hysteresis_release_mi),
            hysteresis_release_rate=_f(hysteresis_release_rate, a.hysteresis_release_rate),
        ),
        mitigation=MitigationConfig(
            rpm_boost=_f(rpm_boost, m.rpm_boost),
            wob_cut=_f(wob_cut, m.wob_cut),
            energy_wob_cut=_f(energy_wob_cut, m.energy_wob_cut),
            ramp_step=_f(ramp_step, m.ramp_step),
            mitigate_rpm_boost=m.mitigate_rpm_boost,
            mitigate_wob_cut=m.mitigate_wob_cut,
            energy_rpm_boost=m.energy_rpm_boost,
            energy_building_wob_cut=m.energy_building_wob_cut,
        ),
        dashboard=cfg.dashboard,
    )
    return Div("Configuration saved", style="color: green; font-weight: bold")


@app.get("/")
def dashboard():
    container_style = "max-width: 1200px; margin: 0 auto; padding: 1rem; font-family: system-ui, sans-serif"
    return (
        Title("Stick-Slip Monitor"),
        Meta(charset="utf-8"),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Link(
            rel="stylesheet",
            href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css",
        ),
        Link(rel="stylesheet", href="?css=1"),
        Div(
            Header(
                H1("STICK-SLIP MONITOR", style="text-align: center; margin: 1rem 0"),
                Div(
                    Button("Start", hx_post="/start", hx_target="#status-bar", hx_swap="innerHTML"),
                    Button("Stop", hx_post="/stop", hx_target="#status-bar", hx_swap="innerHTML"),
                    Button("Restart", hx_post="/restart", hx_target="#status-bar", hx_swap="innerHTML"),
                    style="display: flex; gap: 0.5rem; justify-content: center; margin-bottom: 1rem",
                ),
                Div("STOPPED", style="text-align: center; padding: 0.25rem; background: #d32f2f; color: white; font-weight: bold; border-radius: 4px", id="status-bar"),
                Div(id="alert-bar", hx_get="/alert-bar", hx_trigger="every 2s", hx_swap="outerHTML"),
                Div(
                    Details(
                        Summary("Configuration", style="cursor: pointer; color: #1976d2; font-weight: bold; padding: 0.3rem;"),
                        Div(hx_get="/config-form", hx_trigger="load", hx_swap="innerHTML", id="config-form-content"),
                        style="margin: 0.5rem 0; border: 1px solid #ccc; border-radius: 6px; padding: 0.5rem; background: #fafafa;",
                    ),
                ),
            ),
            Div(
                _sideband_panel(None),
                _energy_panel(None),
                _ssi_panel(None),
                style="display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem",
                id="panels-row",
                hx_get="/panels",
                hx_trigger="every 2s",
                hx_swap="outerHTML",
            ),
            Div(
                _events_panel([]),
                id="events-container",
                hx_get="/events-html",
                hx_trigger="every 2s",
                hx_swap="outerHTML",
            ),
            Div(
                A("Download Report", href="/report", style="display: inline-block; margin-top: 1rem; padding: 0.5rem 1rem; background: #1976d2; color: white; text-decoration: none; border-radius: 4px;"),
                style="text-align: center; margin-top: 1rem",
            ),
            style=container_style,
        ),
    )


@app.get("/css")
def css():
    return Response(_BLINK_CSS, media_type="text/css")


@app.get("/alert-bar")
def alert_bar():
    _, _, _, _, paused = state.snapshot()
    ssi_val = compute_ssi(state._ss.modulation_index) if state._ss else 0.0

    if paused:
        return Div("DRILLING PAUSED — RPM near zero for >6s", cls="drilling-paused", id="alert-bar")
    if ssi_val >= 3.0:
        return Div(f"⚠ STICK-SLIP DETECTED — SSI={ssi_val:.1f}% ⚠", cls="blink-alert", id="alert-bar")
    return Div(id="alert-bar")


@app.get("/panels")
def panels():
    ss, en, m, _, _ = state.snapshot()
    return Div(
        _sideband_panel(ss),
        _energy_panel(en),
        _ssi_panel(ss),
        style="display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem",
        id="panels-row",
        hx_get="/panels",
        hx_trigger="every 2s",
        hx_swap="outerHTML",
    )


@app.get("/events-html")
def events_html():
    _, _, _, events, _ = state.snapshot()
    return Div(
        _events_panel(events),
        id="events-container",
        hx_get="/events-html",
        hx_trigger="every 2s",
        hx_swap="outerHTML",
    )


@app.get("/campbell")
def campbell():
    points = campbell_collector.points
    fm = 0.5
    if points:
        fm = points[-1].fm
    png = render_campbell_diagram(points, fm)
    if png:
        return Response(png, media_type="image/png")
    return Response("No data", media_type="text/plain")


@app.get("/report")
def report():
    all_ss, all_en_raw = state.all_events()
    points = campbell_collector.points
    fm = points[-1].fm if points else 0.5
    html = generate_report(
        ss_events=all_ss,
        energy_events=all_en_raw,
        campbell_points=points,
        theoretical_fm=fm,
        duration_seconds=None,
    )
    return Response(html, media_type="text/html")


@app.post("/start")
def start():
    state.clear()
    _start_pipeline()
    return _status_bar("RUNNING", "#388e3c")


@app.post("/stop")
def stop():
    global _pipeline_thread
    _stop_event.set()
    state.running = False
    if _pipeline_thread and _pipeline_thread.is_alive():
        _pipeline_thread.join(timeout=3.0)
    return _status_bar("STOPPED", "#d32f2f")


@app.post("/restart")
def restart():
    global _pipeline_thread
    _stop_event.set()
    state.running = False
    if _pipeline_thread and _pipeline_thread.is_alive():
        _pipeline_thread.join(timeout=3.0)
    state.clear()
    _start_pipeline()
    return _status_bar("RESTARTING", "#f57c00")


# ---------------------------------------------------------------------------
# Pipeline management
# ---------------------------------------------------------------------------

_pipeline_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _status_bar(text: str, bg: str) -> Div:
    return Div(
        text,
        style=f"text-align: center; padding: 0.25rem; background: {bg}; color: white; font-weight: bold; border-radius: 4px",
        id="status-bar",
    )


def _run_pipeline_thread(config: Config) -> None:
    mc = config.mitigation
    controller = MitigationController(
        baseline_rpm=config.pipeline.baseline_rpm,
        baseline_wob=config.pipeline.baseline_wob,
        sink=state.update_mitigation,
        rpm_boost=mc.rpm_boost,
        wob_cut=mc.wob_cut,
        energy_wob_cut=mc.energy_wob_cut,
        ramp_step=mc.ramp_step,
        mitigate_rpm_boost=mc.mitigate_rpm_boost,
        mitigate_wob_cut=mc.mitigate_wob_cut,
        energy_rpm_boost=mc.energy_rpm_boost,
        energy_building_wob_cut=mc.energy_building_wob_cut,
    )

    def ss_sink(event: StickSlipEvent) -> None:
        state.update_ss(event)
        controller.on_stick_slip(event)

    def energy_sink(event: EnergyEvent) -> None:
        state.update_energy(event)
        controller.on_energy(event)

    state.running = True
    try:
        run_pipeline(
            cfg=config,
            stick_slip_sink=ss_sink,
            energy_sink=energy_sink,
            stop_event=_stop_event,
            campbell_collector=campbell_collector,
            paused_callback=state.set_paused,
        )
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        state.running = False


def _start_pipeline() -> None:
    global _pipeline_thread
    if _pipeline_thread and _pipeline_thread.is_alive():
        _pipeline_thread.join(timeout=3.0)
    _stop_event.clear()
    campbell_collector.clear()
    _pipeline_thread = threading.Thread(
        target=_run_pipeline_thread,
        args=(_config,),
        daemon=True,
    )
    _pipeline_thread.start()


_config: Optional[Config] = None


def run_web(config: Config, host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the FastHTML server. Pipeline begins only when user clicks Start."""
    global _config
    _config = config
    print(f"Stick-slip web UI at http://{host}:{port}")
    try:
        uvicorn.run(app, host=host, port=port, reload=False)
    finally:
        _stop_event.set()
