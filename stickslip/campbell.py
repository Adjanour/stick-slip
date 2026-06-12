"""
Campbell diagram — real-time RPM vs. frequency stability mapping.

Points are (RPM, torsional frequency, SSI) triples collected during
the simulation.  The diagram overlay shows the torsional natural
frequency (horizontal line) and excitation order lines (1×, 2×, 3×).
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CampbellPoint:
    rpm: float
    fm: float
    ssi: float
    timestamp: float
    status: str = ""


class CampbellCollector:
    """Thread-safe buffer of (RPM, fm, SSI) points for the Campbell diagram."""

    def __init__(self, max_points: int = 1000):
        self._lock = threading.Lock()
        self._points: list[CampbellPoint] = []
        self._max = max_points

    def add(self, point: CampbellPoint) -> None:
        with self._lock:
            self._points.append(point)
            if len(self._points) > self._max:
                self._points.pop(0)

    @property
    def points(self) -> list[CampbellPoint]:
        with self._lock:
            return list(self._points)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._points)

    def clear(self) -> None:
        with self._lock:
            self._points.clear()


def render_campbell_diagram(
    points: list[CampbellPoint],
    theoretical_fm: float = 0.5,
) -> Optional[bytes]:
    """Render Campbell diagram to PNG bytes via matplotlib.
    Returns None if matplotlib is not installed.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    if not points:
        return None

    fig, ax = plt.subplots(figsize=(8, 5))

    rpms = np.array([p.rpm for p in points])
    fms = np.array([p.fm for p in points])
    ssis = np.array([p.ssi for p in points])

    sc = ax.scatter(rpms, fms, c=ssis, cmap="RdYlGn_r", s=20, alpha=0.7, edgecolors="none")
    cbar = plt.colorbar(sc, ax=ax, label="SSI (%)")

    y_max = max(fms.max() * 1.5, theoretical_fm * 3, 2.0)
    ax.set_ylim(0, y_max)

    ax.axhline(theoretical_fm, color="blue", linestyle="--", linewidth=1, label=f"Torsional natural freq ({theoretical_fm:.2f} Hz)")
    for order in (1, 2, 3):
        rpm_range = np.linspace(rpms.min(), rpms.max(), 100)
        ax.plot(rpm_range, rpm_range / 60.0 * order, ":", color="gray", alpha=0.5, linewidth=0.8)

    ax.set_xlabel("RPM")
    ax.set_ylabel("Frequency (Hz)")
    ax.set_title("Campbell Diagram — Stick-Slip Stability Map")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()
