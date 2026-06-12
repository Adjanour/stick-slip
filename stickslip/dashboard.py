"""
Live TUI dashboard — real-time display of drilling parameters.

Consumes all three event types and renders a rich terminal layout.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live

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

_STATUS_STYLE = {
    MINIMAL: "green",
    STABLE: "cyan",
    INTENSIFYING: "yellow",
    MITIGATE: "red",
    ENERGY_NORMAL: "green",
    ENERGY_BUILDING: "yellow",
    ENERGY_RELEASE: "red",
}


def _style_status(s: str) -> Text:
    return Text(s, style=_STATUS_STYLE.get(s, "white"))


class Dashboard:
    def __init__(self, max_events: int = 20):
        self._ss: Optional[StickSlipEvent] = None
        self._energy: Optional[EnergyEvent] = None
        self._mitigation: Optional[MitigationSignal] = None
        self._events: deque[tuple[float, str, str]] = deque(maxlen=max_events)
        self._drilling_paused = False
        self._ssi_value = 0.0

    def on_stick_slip(self, event: StickSlipEvent) -> None:
        self._ss = event
        self._ssi_value = compute_ssi(event.modulation_index)
        self._events.append(
            (event.timestamp, "SB", f"{event.status}  SSI={self._ssi_value:.1f}%")
        )

    def on_energy(self, event: EnergyEvent) -> None:
        self._energy = event
        self._events.append(
            (event.timestamp, "EN", f"{event.status}  U={event.energy:.0f}J")
        )

    def on_mitigation(self, signal: MitigationSignal) -> None:
        self._mitigation = signal
        self._events.append((signal.timestamp, "CTRL", signal.reason[:55]))

    def on_paused(self, paused: bool) -> None:
        self._drilling_paused = paused

    def __rich__(self) -> Layout:
        layout = Layout()
        sections = [Layout(self._build_header(), size=3)]
        if self._drilling_paused:
            sections.append(Layout(self._build_paused_bar(), size=3))
        elif self._ss and self._ssi_value >= 3.0:
            sections.append(Layout(self._build_alert_bar(), size=3))
        sections.append(Layout(self._build_columns(), size=8))
        sections.append(Layout(self._build_events(), size=12))
        layout.split_column(*sections)
        return layout

    def _build_header(self) -> Panel:
        title = "STICK-SLIP DASHBOARD"
        if self._drilling_paused:
            title = "⚠  DRILLING PAUSED  ⚠"
        return Panel(
            Text(title, style="bold white on blue", justify="center"),
            border_style="blue",
        )

    def _build_paused_bar(self) -> Panel:
        return Panel(
            Text(" DRILLING PAUSED — RPM near zero for >6s ", style="bold white on red", justify="center"),
            border_style="red",
        )

    def _build_alert_bar(self) -> Panel:
        blink = " █ " if int(time.time() * 2) % 2 else "   "
        return Panel(
            Text(
                f"{blink} STICK-SLIP DETECTED — SSI={self._ssi_value:.1f}% {blink} ",
                style="bold white on red",
                justify="center",
            ),
            border_style="red",
        )

    def _build_columns(self) -> Layout:
        cols = Layout()
        cols.split_row(
            self._build_sideband_panel(),
            self._build_energy_panel(),
            self._build_ssi_panel(),
        )
        return cols

    def _build_sideband_panel(self) -> Panel:
        ss = self._ss
        t = Table.grid(padding=(0, 1))
        t.add_column()
        t.add_column(style="bold")

        if ss is not None:
            t.add_row("Status:", _style_status(ss.status))
            t.add_row("MI:", f"{ss.modulation_index:.4f}")
            t.add_row("dMI/dt:", f"{ss.growth_rate:+.5f}/s")
            t.add_row("Carrier:", f"{ss.carrier_frequency:.2f} Hz")
            t.add_row("FM:", f"{ss.modulation_frequency:.2f} Hz")
            t.add_row("Sidebands:", f"{'Yes' if ss.sidebands_present else 'No'}")
        else:
            t.add_row("Status:", Text("waiting for data…", style="dim"))

        return Panel(t, title="RPM Sideband", border_style="cyan")

    def _build_energy_panel(self) -> Panel:
        en = self._energy
        t = Table.grid(padding=(0, 1))
        t.add_column()
        t.add_column(style="bold")

        if en is not None:
            t.add_row("Status:", _style_status(en.status))
            t.add_row("Energy:", f"{en.energy:.1f} J")
            t.add_row("Peak:", f"{en.peak_energy:.1f} J")
            t.add_row("Drop:", f"{en.drop_ratio:.1%}")
            t.add_row("T_bit:", f"{en.t_bit:.0f} Nm")
            t.add_row("K_total:", f"{en.k_total:.0f} Nm/rad")
            t.add_row("Temp:", f"{en.temp_bit:.1f}°C at {en.bit_depth:.0f}m")
            t.add_row("G derate:", f"{en.g_derating_pct:.2f}%")
            t.add_row("T_off-bottom:", f"{en.t_off_bottom:.0f} Nm")
        else:
            t.add_row("Status:", Text("waiting for data…", style="dim"))

        return Panel(t, title="Torsional Energy", border_style="yellow")

    def _build_ssi_panel(self) -> Panel:
        ssi = self._ssi_value
        cls = ssi_class(ssi)
        style = SSI_STYLE.get(cls, "white")
        t = Table.grid(padding=(0, 1))
        t.add_column()
        t.add_column(style="bold")

        if self._ss is not None:
            t.add_row("SSI:", Text(f"{ssi:.2f}%", style=f"bold {style}"))
            t.add_row("Class:", Text(cls, style=style))
            t.add_row("Description:", Text(ssi_description(ssi), style="dim"))
        else:
            t.add_row("SSI:", Text("—", style="dim"))
            t.add_row("Class:", Text("awaiting data", style="dim"))
            t.add_row("Description:", Text("", style="dim"))

        return Panel(t, title="Stick-Slip Severity Index", border_style=style)

    def _build_events(self) -> Panel:
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim", width=10)
        t.add_column(width=6)
        t.add_column()

        for ts, kind, msg in self._events:
            label = kind
            t.add_row(f"[{ts:7.1f}s]", label, msg)

        if not self._events:
            t.add_row("", Text("no events yet", style="dim"), "")

        return Panel(t, title="Events", border_style="dim")


def run_dashboard(
    dashboard: Dashboard,
    refresh_per_second: int = 4,
) -> Live:
    """Context-managed Rich Live display; caller must pair with stop_dashboard()."""
    live = Live(dashboard, refresh_per_second=refresh_per_second, screen=True)
    live.__enter__()
    return live


def stop_dashboard(live: Live) -> None:
    live.__exit__(None, None, None)
