"""
Console display adapter — separate from the pure core so I/O stays in the shell.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .types import SpectralResult


@dataclass(frozen=True)
class DisplayUpdate:
    timestamp: float
    channel: str
    peak_frequency: float
    severity_index: float


# Per-channel throttle: at most one update per channel per interval
def throttled_display(
    update_rate_hz: float = 1.0,
) -> Callable[[SpectralResult], Optional[DisplayUpdate]]:
    last_emit: dict[str, float] = {}
    min_interval = 1.0 / update_rate_hz

    def _maybe_emit(result: SpectralResult) -> Optional[DisplayUpdate]:
        now = result.timestamp
        previous = last_emit.get(result.channel, 0.0)
        if now - previous >= min_interval:
            last_emit[result.channel] = now
            return DisplayUpdate(
                timestamp=now,
                channel=result.channel,
                peak_frequency=result.peak_frequency,
                severity_index=result.severity_index,
            )
        return None

    return _maybe_emit


def render_display(update: DisplayUpdate) -> None:
    bar_len = 30
    # Scale severity (typically 0–0.002) to fill the bar; ×500 is a visual heuristic
    normalised = min(update.severity_index * 500, 1.0)
    bar = "█" * int(normalised * bar_len) + "░" * (bar_len - int(normalised * bar_len))
    print(
        f"[{update.channel:>6}]  "
        f"t={update.timestamp:>10.3f}s  │  "
        f"Peak: {update.peak_frequency:>6.3f} Hz  │  "
        f"Severity: {update.severity_index:.5f}  │  "
        f"[{bar}]"
    )
