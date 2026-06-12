"""
Stick-Slip Severity Index (SSI) — derived from modulation index.

SSI = MI × 100  (percentage)
  0–1:   None
  1–3:   Mild
  3–5:   Moderate
  5–10:  Severe
  >10:   Critical
"""

from __future__ import annotations

SSI_NONE = "SSI_NONE"
SSI_MILD = "SSI_MILD"
SSI_MODERATE = "SSI_MODERATE"
SSI_SEVERE = "SSI_SEVERE"
SSI_CRITICAL = "SSI_CRITICAL"

SSI_STYLE = {
    SSI_NONE: "green",
    SSI_MILD: "cyan",
    SSI_MODERATE: "yellow",
    SSI_SEVERE: "orange",
    SSI_CRITICAL: "red",
}

SSI_RANGES: list[tuple[float, float, str]] = [
    (0.0, 1.0, SSI_NONE),
    (1.0, 3.0, SSI_MILD),
    (3.0, 5.0, SSI_MODERATE),
    (5.0, 10.0, SSI_SEVERE),
    (10.0, float("inf"), SSI_CRITICAL),
]


def compute_ssi(modulation_index: float) -> float:
    """SSI as a percentage: 100 × modulation_index."""
    return modulation_index * 100.0


def ssi_class(ssi: float) -> str:
    for lo, hi, label in SSI_RANGES:
        if lo <= ssi < hi:
            return label
    return SSI_NONE


def ssi_description(ssi: float) -> str:
    c = ssi_class(ssi)
    descriptions = {
        SSI_NONE: "No stick-slip",
        SSI_MILD: "Mild torsional vibration",
        SSI_MODERATE: "Moderate stick-slip — monitor",
        SSI_SEVERE: "Severe stick-slip — consider mitigation",
        SSI_CRITICAL: "Critical stick-slip — immediate action required",
    }
    return descriptions.get(c, "Unknown")
