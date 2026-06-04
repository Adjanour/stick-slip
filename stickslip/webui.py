"""
FastHTML web UI — real-time monitoring dashboard with HTMX polling.

Run via: stickslip --web
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

import uvicorn

from fasthtml.common import (
    Button,
    Div,
    FastHTML,
    H1,
    H3,
    Header,
    Link,
    Meta,
    Span,
    Table,
    Td,
    Th,
    Title,
    Tr,
)

from .cli import run as run_pipeline
from .config import Config
from .mitigation import MitigationController
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

    def update_ss(self, event: StickSlipEvent) -> None:
        with self._lock:
            self._ss = event
            self._events.append(
                (
                    event.timestamp,
                    "SB",
                    f"{event.status}  MI={event.modulation_index:.4f}",
                )
            )
            self._last_update = time.time()

    def update_energy(self, event: EnergyEvent) -> None:
        with self._lock:
            self._energy = event
            self._events.append(
                (event.timestamp, "EN", f"{event.status}  U={event.energy:.0f}J")
            )
            self._last_update = time.time()

    def update_mitigation(self, signal: MitigationSignal) -> None:
        with self._lock:
            self._mitigation = signal
            self._events.append((signal.timestamp, "CTRL", signal.reason[:55]))
            self._last_update = time.time()

    def snapshot(self) -> tuple:
        with self._lock:
            return (
                self._ss,
                self._energy,
                self._mitigation,
                list(self._events),
            )

    @property
    def running(self) -> bool:
        return self._running

    @running.setter
    def running(self, value: bool) -> None:
        self._running = value


state = SharedState()

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


def _mitigation_panel(m: Optional[MitigationSignal]) -> Div:
    rows = []
    if m is not None:
        rows = [
            Tr(Td("RPM:"), Td(f"{m.rpm_setpoint:.1f}", style="font-weight: bold")),
            Tr(Td("WOB:"), Td(f"{m.wob_setpoint:.0f} N", style="font-weight: bold")),
            Tr(Td("Reason:"), Td(m.reason[:50], style="color: gray; font-size: 0.9em")),
        ]
    else:
        rows = [
            Tr(Td("RPM:"), Td("—", style="color: gray")),
            Tr(Td("WOB:"), Td("—", style="color: gray")),
            Tr(Td("Reason:"), Td("awaiting first event", style="color: gray")),
        ]

    return Div(
        H3("Mitigation Setpoints", style="margin: 0 0 0.5rem 0"),
        Table(*rows, style="width: 100%"),
        style="border: 1px solid #00cc66; border-radius: 6px; padding: 1rem; flex: 1; min-width: 200px",
        id="mitigation-panel",
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
            Tr(
                Td(
                    "no events yet",
                    style="color: gray; text-align: center",
                    colspan="3",
                )
            )
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
        Div(
            Header(
                H1("STICK-SLIP MONITOR", style="text-align: center; margin: 1rem 0"),
                Div(
                    Button(
                        "Start",
                        hx_post="/start",
                        hx_target="#status-bar",
                        hx_swap="innerHTML",
                    ),
                    Button(
                        "Stop",
                        hx_post="/stop",
                        hx_target="#status-bar",
                        hx_swap="innerHTML",
                    ),
                    Button(
                        "Restart",
                        hx_post="/restart",
                        hx_target="#status-bar",
                        hx_swap="innerHTML",
                    ),
                    style="display: flex; gap: 0.5rem; justify-content: center; margin-bottom: 1rem",
                ),
                Div(
                    "STOPPED",
                    style="text-align: center; padding: 0.25rem; background: #d32f2f; color: white; font-weight: bold; border-radius: 4px",
                    id="status-bar",
                ),
            ),
            Div(
                _sideband_panel(None),
                _energy_panel(None),
                _mitigation_panel(None),
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
            style=container_style,
        ),
    )


@app.get("/panels")
def panels():
    ss, en, m, _ = state.snapshot()
    return Div(
        _sideband_panel(ss),
        _energy_panel(en),
        _mitigation_panel(m),
        style="display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem",
        id="panels-row",
        hx_get="/panels",
        hx_trigger="every 2s",
        hx_swap="outerHTML",
    )


@app.get("/events-html")
def events_html():
    _, _, _, events = state.snapshot()
    return Div(
        _events_panel(events),
        id="events-container",
        hx_get="/events-html",
        hx_trigger="every 2s",
        hx_swap="outerHTML",
    )


@app.post("/start")
def start():
    _start_pipeline()
    return _status_bar("RUNNING", "#388e3c")


@app.post("/stop")
def stop():
    _stop_pipeline()
    return _status_bar("STOPPED", "#d32f2f")


@app.post("/restart")
def restart():
    _stop_pipeline()
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
        )
    except Exception:
        import traceback

        traceback.print_exc()
    finally:
        state.running = False


def _start_pipeline() -> None:
    global _pipeline_thread
    if _pipeline_thread and _pipeline_thread.is_alive():
        return
    _stop_event.clear()
    _pipeline_thread = threading.Thread(
        target=_run_pipeline_thread,
        args=(_config,),
        daemon=True,
    )
    _pipeline_thread.start()


def _stop_pipeline() -> None:
    _stop_event.set()
    state.running = False


_config: Optional[Config] = None


def run_web(config: Config, host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the FastHTML server, begins the pipeline immediately, blocks on uvicorn."""
    global _config
    _config = config
    print(f"Stick-slip web UI at http://{host}:{port}")
    _start_pipeline()
    uvicorn.run(app, host=host, port=port, reload=False)
