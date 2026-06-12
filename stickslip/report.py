"""
Downloadable HTML report — events timeline, SSI history, Campbell diagram.
"""

from __future__ import annotations

import base64
from typing import Optional

from .campbell import CampbellPoint, render_campbell_diagram

_NO_EVENTS_ROW = '<tr><td colspan="5" style="color: gray; text-align: center">No events</td></tr>'
from .ssi import compute_ssi, ssi_class, ssi_description, SSI_STYLE


def _severity_badge(status: str) -> str:
    styles = {
        "MINIMAL": "background: #4caf50; color: white",
        "STABLE": "background: #00bcd4; color: white",
        "INTENSIFYING": "background: #ff9800; color: white",
        "MITIGATE": "background: #f44336; color: white",
        "SSI_NONE": "background: #4caf50; color: white",
        "SSI_MILD": "background: #00bcd4; color: white",
        "SSI_MODERATE": "background: #ff9800; color: white",
        "SSI_SEVERE": "background: #ff5722; color: white",
        "SSI_CRITICAL": "background: #f44336; color: white",
    }
    style = styles.get(status, "background: #9e9e9e; color: white")
    return f'<span style="{style}; padding: 2px 8px; border-radius: 4px; font-size: 0.85em">{status}</span>'


def _count_by_severity(events: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ev in events:
        s = ev.status if hasattr(ev, "status") else str(ev)
        counts[s] = counts.get(s, 0) + 1
    return counts


def generate_report(
    ss_events: list,
    energy_events: list,
    campbell_points: list[CampbellPoint],
    theoretical_fm: float = 0.5,
    duration_seconds: Optional[float] = None,
) -> str:
    campbell_img: Optional[str] = None
    png = render_campbell_diagram(campbell_points, theoretical_fm)
    if png:
        campbell_img = base64.b64encode(png).decode("ascii")

    # Compute elapsed duration from event timestamps if not provided
    if duration_seconds is None and ss_events:
        start = min(ev.timestamp for ev in ss_events)
        end = max(ev.timestamp for ev in ss_events)
        duration_seconds = end - start
    elif duration_seconds is None:
        duration_seconds = 0.0

    all_ts = [ev.timestamp for ev in ss_events] + [ev.timestamp for ev in energy_events]
    t0 = min(all_ts) if all_ts else 0.0

    ss_rows = ""
    for ev in ss_events:
        ssi = compute_ssi(ev.modulation_index)
        cls = ssi_class(ssi)
        t_rel = ev.timestamp - t0
        ss_rows += f"""
        <tr>
          <td>{t_rel:.1f}s</td>
          <td>{_severity_badge(ev.status)}</td>
          <td>{ssi:.2f}%</td>
          <td>{_severity_badge(cls)}</td>
          <td>{ev.modulation_index:.4f}</td>
          <td>{ev.growth_rate:+.5f}/s</td>
        </tr>"""

    en_rows = ""
    for ev in energy_events:
        t_rel = ev.timestamp - t0
        en_rows += f"""
        <tr>
          <td>{t_rel:.1f}s</td>
          <td>{_severity_badge(ev.status)}</td>
          <td>{ev.energy:.1f}J</td>
          <td>{ev.peak_energy:.1f}J</td>
          <td>{ev.drop_ratio:.1%}</td>
          <td>{ev.t_bit:.0f}Nm</td>
        </tr>"""

    # Build compact timeline: consecutive same-status events → one episode
    timeline_rows = ""
    if ss_events:
        ep_start = ss_events[0].timestamp
        ep_status = ss_events[0].status
        ep_peak_ssi = compute_ssi(ss_events[0].modulation_index)
        prev_ev = ss_events[0]
        for ev in ss_events[1:]:
            ssi = compute_ssi(ev.modulation_index)
            if ev.status != ep_status:
                t_start_rel = ep_start - t0
                t_end_rel = prev_ev.timestamp - t0
                timeline_rows += f"""
        <tr>
          <td>{t_start_rel:.1f}s</td>
          <td>{t_end_rel:.1f}s</td>
          <td>{t_end_rel - t_start_rel:.1f}s</td>
          <td>{_severity_badge(ep_status)}</td>
          <td>{ep_peak_ssi:.2f}%</td>
        </tr>"""
                ep_start = ev.timestamp
                ep_status = ev.status
                ep_peak_ssi = ssi
            else:
                ep_peak_ssi = max(ep_peak_ssi, ssi)
            prev_ev = ev
        t_start_rel = ep_start - t0
        t_end_rel = prev_ev.timestamp - t0
        timeline_rows += f"""
        <tr>
          <td>{t_start_rel:.1f}s</td>
          <td>{t_end_rel:.1f}s</td>
          <td>{t_end_rel - t_start_rel:.1f}s</td>
          <td>{_severity_badge(ep_status)}</td>
          <td>{ep_peak_ssi:.2f}%</td>
        </tr>"""

    sev_counts = _count_by_severity(ss_events)
    sev_rows = ""
    for sev in ["MINIMAL", "STABLE", "INTENSIFYING", "MITIGATE"]:
        c = sev_counts.get(sev, 0)
        sev_rows += f"<tr><td>{sev}</td><td>{c}</td></tr>"

    campbell_section = ""
    if campbell_img:
        campbell_section = f"""
        <h2>Campbell Diagram</h2>
        <p>RPM vs. torsional frequency — points coloured by SSI (green = low, red = high).</p>
        <img src="data:image/png;base64,{campbell_img}"
             style="max-width: 100%; border: 1px solid #ccc; border-radius: 6px;" />"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Stick-Slip Simulation Report</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 2rem; color: #333; }}
  h1, h2 {{ border-bottom: 2px solid #1976d2; padding-bottom: 0.3rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.85em; }}
  th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #ddd; white-space: nowrap; }}
  th {{ background: #1976d2; color: white; position: sticky; top: 0; }}
  tr:hover {{ background: #f5f5f5; }}
  .summary {{ display: flex; gap: 2rem; flex-wrap: wrap; }}
  .stat {{ background: #e3f2fd; padding: 1rem; border-radius: 8px; flex: 1; min-width: 120px; }}
  .stat h3 {{ margin: 0 0 0.3rem 0; font-size: 0.9em; color: #555; }}
  .stat .value {{ font-size: 1.8em; font-weight: bold; color: #1976d2; }}
  .scroll {{ max-height: 500px; overflow-y: auto; border: 1px solid #ddd; border-radius: 4px; }}
  .no-print {{ display: none; }}
  @media print {{
    @page {{ margin: 1.5cm; size: A4 landscape; }}
    body {{ padding: 0; max-width: none; font-size: 9pt; }}
    .scroll {{ max-height: none !important; overflow: visible !important; }}
    .no-print {{ display: none !important; }}
    tr:hover {{ background: inherit; }}
    th {{ background: #1976d2 !important; color: white !important; }}
  }}
</style>
<script>
function downloadPDF() {{
  window.print();
}}
</script>
</head>
<body>
<h1>Stick-Slip Simulation Report</h1>
<div style="text-align: right; margin-bottom: 0.5rem">
  <button class="no-print" onclick="downloadPDF()" style="padding: 0.5rem 1rem; background: #1976d2; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 1em;">
    ⬇ Download PDF
  </button>
</div>
<div class="summary">
  <div class="stat"><h3>Duration</h3><div class="value">{duration_seconds:.0f}s</div></div>
  <div class="stat"><h3>Total Events</h3><div class="value">{len(ss_events)}</div></div>
  <div class="stat"><h3>Energy Events</h3><div class="value">{len(energy_events)}</div></div>
</div>

<h2>Severity Distribution (Sideband)</h2>
<table><tr><th>Level</th><th>Count</th></tr>{sev_rows}</table>

<h2>Stick-Slip Timeline</h2>
<p>Consecutive same-status events grouped into episodes.</p>
<table>
  <tr><th>Start</th><th>End</th><th>Duration</th><th>Status</th><th>Peak SSI</th></tr>
  {timeline_rows or _NO_EVENTS_ROW}
</table>

<h2>Sideband Events (all {len(ss_events)})</h2>
<div class="scroll">
<table>
  <tr><th>Time</th><th>Status</th><th>SSI</th><th>SSI Class</th><th>MI</th><th>dMI/dt</th></tr>
  {ss_rows}
</table>
</div>

<h2>Energy Events (all {len(energy_events)})</h2>
<div class="scroll">
<table>
  <tr><th>Time</th><th>Status</th><th>Energy</th><th>Peak</th><th>Drop</th><th>T_bit</th></tr>
  {en_rows}
</table>
</div>

{campbell_section}

<footer style="margin-top: 3rem; color: #888; font-size: 0.85em; border-top: 1px solid #ccc; padding-top: 1rem;">
  Generated by stickslip pipeline &mdash; {len(ss_events)} sideband events, {len(energy_events)} energy events,
  {len(campbell_points)} Campbell data points.
</footer>
</body>
</html>"""
