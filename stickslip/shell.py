"""
Effectful shell — I/O lives here so the core stays pure.

Sources: SharedCsvSource (historical replay), and the D‑WIS OPC‑UA connector
for live competition data (see dwis.py).

Competition design intent:
  Historical CSV data is streamed as if from a digital twin.  On competition
  day, the team's controller connects to the OpenLab simulator through the
  D‑WIS OPC‑UA gateway instead of reading a file.  Both sources implement
  the same next_rpm() / next_torque() / advance() interface so the pipeline
  is source-agnostic.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np


DEFAULT_CSV_PATH = Path(__file__).resolve().parent.parent / "test.csv"


class SharedCsvSource:
    """Thread-safe CSV source that synchronizes two detection tracks via Barrier.

    Both tracks share one source and a threading.Barrier(2, action=...).
    After each chunk is read (both tracks), the barrier action advances the
    shared index and sleeps per sample_rate, keeping the tracks in lockstep.

    When the CSV is exhausted, the last row value is held (the pad-at-end
    behaviour) so the pipeline sees a steady DC input rather than a short
    chunk.
    """

    _NAN_PAD = 0.0

    def __init__(
        self,
        path: Path = DEFAULT_CSV_PATH,
        chunk_size: int = 10,
        sample_rate: float = 50.0,
    ):
        raw = np.genfromtxt(path, delimiter=",", names=True, dtype=None, encoding=None)
        if raw.ndim != 1 or raw.dtype.names is None:
            raise ValueError(
                f"CSV must have named columns, got shape={raw.shape}"
            )
        for col in ("bit_rpm", "torque"):
            if col not in raw.dtype.names:
                raise KeyError(f"CSV missing required column '{col}'")
        rpm = raw["bit_rpm"].astype(np.float64)
        torque = raw["torque"].astype(np.float64)
        # Replace NaN / inf with the pad value
        rpm[~np.isfinite(rpm)] = self._NAN_PAD
        torque[~np.isfinite(torque)] = self._NAN_PAD
        self._rpm = rpm
        self._torque = torque
        self._n = len(self._rpm)
        self._chunk_size = chunk_size
        self._dt = 1.0 / sample_rate
        self._index = 0
        self._lock = threading.Lock()

    @property
    def total_values(self) -> int:
        return self._n

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    def _read_chunk(self, arr: np.ndarray) -> np.ndarray:
        with self._lock:
            i = self._index
            end = i + self._chunk_size
            if end <= self._n:
                return arr[i:end].copy()
            # Pad-at-end: fill remaining with last valid value
            n_avail = max(0, self._n - i)
            if n_avail == 0:
                return np.full(self._chunk_size, arr[-1], dtype=np.float64)
            chunk = np.empty(self._chunk_size, dtype=np.float64)
            chunk[:n_avail] = arr[i:]
            chunk[n_avail:] = arr[-1]
            return chunk

    def next_rpm(self) -> np.ndarray:
        return self._read_chunk(self._rpm)

    def next_torque(self) -> np.ndarray:
        return self._read_chunk(self._torque)

    def advance(self) -> None:
        with self._lock:
            self._index += self._chunk_size
            sleep_time = self._dt * self._chunk_size
            if sleep_time > 0:
                time.sleep(sleep_time)
