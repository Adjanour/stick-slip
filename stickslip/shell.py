"""
Effectful shell — I/O lives here so the core stays pure.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Generator, Optional

import numpy as np


SensorReader = Callable[[], float]
DEFAULT_CSV_PATH = Path(__file__).resolve().parent.parent / "test.csv"


def simulate_signal(
    base: float = 120.0,
    stick_slip_hz: float = 0.5,
    stick_slip_amplitude: float = 30.0,
    noise_std: float = 2.0,
) -> SensorReader:
    def _read() -> float:
        t = time.time()
        return (
            base
            + stick_slip_amplitude * np.sin(2 * np.pi * stick_slip_hz * t)
            + np.random.normal(0.0, noise_std)
        )

    return _read


# Loads a single column from CSV using numpy's structured array reader
def _load_csv_column(path: Path) -> np.ndarray:
    data = np.genfromtxt(path, delimiter=",", names=True, dtype=None, encoding=None)
    if "bit_rpm" not in data.dtype.names:
        raise KeyError("CSV source requires a 'bit_rpm' column")
    return data["bit_rpm"].astype(np.float64)


# Closure-based reader: advances through values, holds last on exhaustion
def _values_reader(values: np.ndarray) -> SensorReader:
    values = np.asarray(values, dtype=np.float64)
    if len(values) == 0:
        raise ValueError("CSV source requires at least one numeric value")

    index = 0
    n = len(values)

    def _read() -> float:
        nonlocal index
        value = float(values[index])
        if index < n - 1:
            index += 1
        return value

    return _read


def csv_source(data: Optional[np.ndarray] = None) -> SensorReader:
    values = data if data is not None else _load_csv_column(DEFAULT_CSV_PATH)
    return _values_reader(values)


# Yields fixed-size chunks, sleeping to maintain the target sample rate
def csv_chunk_stream(
    sample_rate: float = 50.0,
    chunk_size: int = 10,
    data: Optional[np.ndarray] = None,
) -> Generator[tuple[np.ndarray, float], None, None]:
    reader = csv_source(data)
    dt = 1.0 / sample_rate

    while True:
        t0 = time.time()
        chunk = np.array([reader() for _ in range(chunk_size)], dtype=np.float64)
        yield chunk, t0

        elapsed = time.time() - t0
        sleep_time = dt * chunk_size - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)


def sensor_stream(
    sample_rate: float = 50.0,
    channels: tuple[str, ...] = ("RPM", "Torque"),
    sources: Optional[dict[str, SensorReader]] = None,
) -> Generator[tuple[dict[str, float], float], None, None]:
    readers = sources or {
        "RPM": simulate_signal(120.0, 0.5, 30.0, 2.0),
        "Torque": simulate_signal(1800.0, 0.5, 120.0, 8.0),
    }
    dt = 1.0 / sample_rate

    while True:
        t0 = time.time()
        readings = {channel: float(readers[channel]()) for channel in channels}
        yield readings, t0

        elapsed = time.time() - t0
        sleep_time = dt - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)
