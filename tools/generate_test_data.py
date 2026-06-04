#!/usr/bin/env python3
"""
Generate realistic stick-slip drilling data with coupled RPM-torque dynamics.

Models the drillstring as a torsional spring-damper with Coulomb bit friction,
producing asymmetric stick-slip cycles across five severity phases.

Physics:
  - Surface drive maintains constant RPM (ω_s = 120 RPM)
  - Bit experiences Stribeck friction (static > dynamic)
  - During stick (RPM ↓): torque builds up linearly as string twists
  - During slip (RPM ↑): stored energy releases, torque drops
  - T_surface = T_base + K·θ_twist + C·Δω

Output matches format expected by stickslip.shell.csv_chunk_stream.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT_ROOT / "test.csv"
RNG = np.random.default_rng(42)

# ---------------------------------------------------------------------------
# Physical drillstring parameters
# ---------------------------------------------------------------------------
K_TORSION = 1500.0  # torsional stiffness (Nm/rad) — combined pipe + BHA
C_DAMPING = 20.0  # structural damping (Nm·s/rad)
SURFACE_RPM = 120.0  # top drive setpoint
SURFACE_OMEGA = SURFACE_RPM * 2 * np.pi / 60.0  # rad/s

FRICTION_BASE = 3500.0  # continuous wellbore friction torque (Nm)

# Measurement noise
NOISE_RPM_STD = 1.5
NOISE_TORQUE_STD = 30.0


def _severity(t: float) -> float:
    """Phase-based stick-slip severity envelope."""
    if t < 15.0:
        return 0.02
    if t < 30.0:
        return 0.22
    if t < 45.0:
        return 0.50
    if t < 65.0:
        return 0.82
    if t < 80.0:
        return 0.55
    return max(0.55 - 0.025 * (t - 80.0), 0.02)


def generate(
    duration: float = 100.0, dt: float = 0.1
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = int(round(duration / dt))
    t = np.arange(n, dtype=np.float64) * dt

    rpm = np.empty(n, dtype=np.float64)
    torque = np.empty(n, dtype=np.float64)

    theta_twist = 0.0  # accumulated twist angle (rad)
    f_natural = 0.35  # torsional natural frequency (Hz)

    # Duty cycle of the stick phase (remainder = slip phase)
    STICK_FRAC = 0.65

    for i in range(n):
        ti = t[i]
        sev = _severity(ti)

        # ---- Asymmetric RPM waveform ----
        phase = (ti * f_natural) % 1.0
        if phase < STICK_FRAC:
            mod = -phase / STICK_FRAC  # -1 → 0 over stick phase
        else:
            slip = (phase - STICK_FRAC) / (1.0 - STICK_FRAC)
            mod = slip  # 0 → 1 over slip phase

        max_drop = SURFACE_RPM * 0.92
        max_rise = SURFACE_RPM * 1.7
        bit_rpm = SURFACE_RPM + sev * np.where(mod < 0, mod * max_drop, mod * max_rise)
        bit_rpm = max(bit_rpm, 0.0)

        # ---- Coupled torque ----
        omega_bit = bit_rpm * 2 * np.pi / 60.0
        delta_omega = SURFACE_OMEGA - omega_bit

        theta_twist += delta_omega * dt
        theta_twist = np.clip(theta_twist, -0.3, 2.5)

        T_surface = FRICTION_BASE + K_TORSION * theta_twist + C_DAMPING * delta_omega
        T_surface = max(T_surface, 100.0)

        # ---- Add measurement noise ----
        noise_scale = 1.0 + 2.5 * sev
        rpm[i] = bit_rpm + RNG.normal(0, NOISE_RPM_STD * noise_scale)
        torque[i] = T_surface + RNG.normal(0, NOISE_TORQUE_STD * noise_scale)

    rpm = np.maximum(rpm, 0.0)
    return t, rpm, torque


def save(t: np.ndarray, rpm: np.ndarray, torque: np.ndarray, path: Path = OUTPUT):
    data = np.column_stack([t, rpm, torque])
    np.savetxt(
        path,
        data,
        delimiter=",",
        header="timestamp,bit_rpm,torque",
        comments="",
        fmt=["%.1f", "%.2f", "%.2f"],
    )


def print_stats(t, rpm, torque):
    print(f"Rows: {len(t)} ({t[-1]:.0f}s at {1 / (t[1] - t[0]):.0f} Hz)")
    print(
        f"bit_rpm: mean={rpm.mean():.1f}  min={rpm.min():.1f}  max={rpm.max():.1f}  std={rpm.std():.1f}"
    )
    print(
        f"torque:  mean={torque.mean():.1f}  min={torque.min():.1f}  max={torque.max():.1f}  std={torque.std():.1f}"
    )

    phases = [
        (0, 15, "stable"),
        (15, 30, "mild"),
        (30, 45, "moderate"),
        (45, 65, "severe"),
        (65, 80, "recovery1"),
        (80, 100, "recovery2"),
    ]
    for start, end, label in phases:
        mask = (t >= start) & (t < end)
        print(
            f"  {label:>12s} ({start:3.0f}-{end:3.0f}s): "
            f"RPM {rpm[mask].mean():5.0f}±{rpm[mask].std():4.0f}  "
            f"Tq {torque[mask].mean():5.0f}±{torque[mask].std():4.0f}"
        )
    print(
        f"  Above t_off_bottom (5000 Nm): {(torque > 5000).sum()} of {len(t)} samples"
        f" ({(torque > 5000).mean() * 100:.1f}%)"
    )


if __name__ == "__main__":
    print("Generating realistic stick-slip data…")
    t, rpm, torque = generate(duration=100.0, dt=0.1)
    save(t, rpm, torque)
    print_stats(t, rpm, torque)
    print(f"Written: {OUTPUT}")
