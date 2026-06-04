"""
Textual TUI dashboard — interactive live monitoring with keyboard input.

Run via: stickslip --tui
"""

from __future__ import annotations

from collections import deque
from queue import Queue
from threading import Event, Thread
from typing import Optional

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Static

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

_STATUS_STYLE = {
    MINIMAL: "green",
    STABLE: "cyan",
    INTENSIFYING: "yellow",
    MITIGATE: "red",
    ENERGY_NORMAL: "green",
    ENERGY_BUILDING: "yellow",
    ENERGY_RELEASE: "red",
}


def _style(s: str) -> Text:
    return Text(s, style=_STATUS_STYLE.get(s, "white"))


CSS = """
StickSlipTUI {
    background: $surface;
}

#panels-row {
    height: 50%;
    min-height: 15;
}

.panel-box {
    width: 1fr;
    height: 100%;
    padding: 0 1;
    border: solid $primary;
}

#events-box {
    height: 50%;
    min-height: 10;
    border: solid $secondary;
    padding: 0 1;
}

#events-log {
    height: 100%;
}

#status-bar {
    height: 1;
    content-align: center middle;
    background: $accent;
    color: $text;
}

.running {
    background: green;
}
.stopped {
    background: red;
}
"""


class PanelWidget(Static):
    """A single panel that displays rich renderable content."""

    def update_content(self, renderable: object) -> None:
        self.update(renderable)


class EventLine:
    __slots__ = ("timestamp", "kind", "message")

    def __init__(self, timestamp: float, kind: str, message: str):
        self.timestamp = timestamp
        self.kind = kind
        self.message = message


class StickSlipTUI(App):
    """Textual app: keyboard-driven live dashboard with pipeline thread + queue-based events."""

    CSS = CSS

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("s", "toggle", "Start/Stop"),
        Binding("r", "restart", "Restart"),
        Binding("p", "params", "Parameters"),
    ]

    status_text = reactive("STOPPED")
    status_color = reactive("red")

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._queue: Queue = Queue()
        self._stop_event = Event()
        self._paused = False
        self._thread: Optional[Thread] = None
        self._ss: Optional[StickSlipEvent] = None
        self._energy: Optional[EnergyEvent] = None
        self._mitigation: Optional[MitigationSignal] = None
        self._events: deque = deque(maxlen=50)
        self._n_cycles = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="status-bar")
        with Horizontal(id="panels-row"):
            yield PanelWidget(id="sideband-panel", classes="panel-box")
            yield PanelWidget(id="energy-panel", classes="panel-box")
            yield PanelWidget(id="mitigation-panel", classes="panel-box")
        with Vertical(id="events-box"):
            yield Static(id="events-log")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1 / 4, self._drain_queue)
        self._start_pipeline()

    def _render_sideband(self) -> Panel:
        ss = self._ss
        t = Table.grid(padding=(0, 1))
        t.add_column()
        t.add_column(style="bold")

        if ss is not None:
            t.add_row("Status:", _style(ss.status))
            t.add_row("MI:", f"{ss.modulation_index:.4f}")
            t.add_row("dMI/dt:", f"{ss.growth_rate:+.5f}/s")
            t.add_row("Carrier:", f"{ss.carrier_frequency:.2f} Hz")
            t.add_row("FM:", f"{ss.modulation_frequency:.2f} Hz")
            t.add_row("Sidebands:", "Yes" if ss.sidebands_present else "No")
        else:
            t.add_row("Status:", Text("waiting for data…", style="dim"))

        return Panel(t, title="RPM Sideband", border_style="cyan")

    def _render_energy(self) -> Panel:
        en = self._energy
        t = Table.grid(padding=(0, 1))
        t.add_column()
        t.add_column(style="bold")

        if en is not None:
            t.add_row("Status:", _style(en.status))
            t.add_row("Energy:", f"{en.energy:.1f} J")
            t.add_row("Peak:", f"{en.peak_energy:.1f} J")
            t.add_row("Drop:", f"{en.drop_ratio:.1%}")
            t.add_row("T_bit:", f"{en.t_bit:.0f} Nm")
            t.add_row("K_total:", f"{en.k_total:.0f} Nm/rad")
            t.add_row("Temp:", f"{en.temp_bit:.1f}°C at {en.bit_depth:.0f}m")
            t.add_row("G derate:", f"{en.g_derating_pct:.2f}%")
            t.add_row("T_off-bot:", f"{en.t_off_bottom:.0f} Nm")
        else:
            t.add_row("Status:", Text("waiting for data…", style="dim"))

        return Panel(t, title="Torsional Energy", border_style="yellow")

    def _render_mitigation(self) -> Panel:
        m = self._mitigation
        t = Table.grid(padding=(0, 1))
        t.add_column()
        t.add_column(style="bold")

        if m is not None:
            t.add_row("RPM:", f"{m.rpm_setpoint:.1f}")
            t.add_row("WOB:", f"{m.wob_setpoint:.0f} N")
            t.add_row("Reason:", Text(m.reason[:50], style="dim"))
        else:
            t.add_row("RPM:", Text("—", style="dim"))
            t.add_row("WOB:", Text("—", style="dim"))
            t.add_row("Reason:", Text("awaiting first event", style="dim"))

        return Panel(t, title="Mitigation Setpoints", border_style="green")

    def _render_events(self) -> Panel:
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim", width=10)
        t.add_column(width=6)
        t.add_column()

        for ev in self._events:
            label = ev.kind
            t.add_row(f"[{ev.timestamp:7.1f}s]", label, ev.message)

        if not self._events:
            t.add_row("", Text("no events yet", style="dim"), "")

        return Panel(t, title="Events", border_style="dim")

    def _update_panels(self) -> None:
        self.query_one("#sideband-panel").update_content(self._render_sideband())
        self.query_one("#energy-panel").update_content(self._render_energy())
        self.query_one("#mitigation-panel").update_content(self._render_mitigation())
        self.query_one("#events-log").update(self._render_events())

    def _drain_queue(self) -> None:
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                kind, data = item
                if kind == "ss":
                    self._ss = data
                    self._events.append(
                        EventLine(
                            data.timestamp,
                            "SB",
                            f"{data.status}  MI={data.modulation_index:.4f}",
                        )
                    )
                elif kind == "energy":
                    self._energy = data
                    self._events.append(
                        EventLine(
                            data.timestamp, "EN", f"{data.status}  U={data.energy:.0f}J"
                        )
                    )
                elif kind == "mitigation":
                    self._mitigation = data
                    self._events.append(
                        EventLine(data.timestamp, "CTRL", data.reason[:55])
                    )
            except Exception:
                break
        self._update_panels()

    def _on_ss(self, event: StickSlipEvent) -> None:
        self._queue.put(("ss", event))

    def _on_energy(self, event: EnergyEvent) -> None:
        self._queue.put(("energy", event))

    def _on_mitigation(self, signal: MitigationSignal) -> None:
        self._queue.put(("mitigation", signal))

    def _run_pipeline_thread(self) -> None:
        mc = self.config.mitigation
        controller = MitigationController(
            baseline_rpm=self.config.pipeline.baseline_rpm,
            baseline_wob=self.config.pipeline.baseline_wob,
            sink=self._on_mitigation,
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
            self._on_ss(event)
            controller.on_stick_slip(event)

        def energy_sink(event: EnergyEvent) -> None:
            self._on_energy(event)
            controller.on_energy(event)

        try:
            run_pipeline(
                cfg=self.config,
                stick_slip_sink=ss_sink,
                energy_sink=energy_sink,
                stop_event=self._stop_event,
            )
        except Exception as exc:
            import traceback

            traceback.print_exc()
        finally:
            self.call_from_thread(self._on_pipeline_done)

    def _on_pipeline_done(self) -> None:
        self.status_text = "COMPLETED"
        self.status_color = "blue"
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        bar = self.query_one("#status-bar")
        bar.update(f" {self.status_text} ")
        bar.styles.background = self.status_color

    def _start_pipeline(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ss = None
        self._energy = None
        self._mitigation = None
        self._events.clear()
        self.status_text = "RUNNING"
        self.status_color = "green"
        self._update_status_bar()
        self._thread = Thread(target=self._run_pipeline_thread, daemon=True)
        self._thread.start()

    def _stop_pipeline(self) -> None:
        self._stop_event.set()
        self.status_text = "STOPPED"
        self.status_color = "red"
        self._update_status_bar()

    def action_toggle(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop_pipeline()
        else:
            self._start_pipeline()

    def action_restart(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=2.0)
        self._start_pipeline()

    def action_params(self) -> None:
        self.status_text = "PARAMETERS — use --config or edit config.toml"
        self.status_color = "magenta"
        self._update_status_bar()

    def action_quit(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
        self.exit()


def run_tui(config: Config) -> None:
    """Entry point: instantiate and run the Textual app (blocking)."""
    app = StickSlipTUI(config)
    app.run()
