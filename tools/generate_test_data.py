#!/usr/bin/env python3
"""
Generate realistic stick-slip drilling data with smooth phase transitions.

Models the drillstring as a torsional spring-damper with Stribeck bit friction
(Coulomb + velocity-weakening), producing asymmetric stick-slip cycles with
two torsional modes across five clear phases:

  0–20 s   Normal drilling        RPM ~120, torque ~3500 Nm, low noise
 20–40 s   Gradual onset          RPM oscillations grow, torque cycles begin
 40–65 s   Full stick-slip        RPM swings 0→200 RPM, torque 3500→8000+ Nm
 65–80 s   Mitigation taking hold Oscillations dampen, torque range narrows
 80–100 s  Recovery / stable      Back to baseline

Output format: timestamp,bit_rpm,torque  (matches csv_chunk_stream)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT_ROOT / "test.csv"
RNG = np.random.default_rng(42)

# Physical drillstring parameters
K_TORSION = 1500.0       # torsional stiffness (Nm/rad)
C_DAMPING = 20.0         # structural damping (Nm·s/rad)
SURFACE_RPM = 120.0      # top drive setpoint
SURFACE_OMEGA = SURFACE_RPM * 2 * np.pi / 60.0
FRICTION_BASE = 3500.0   # baseline wellbore friction (Nm)
F_NATURAL_1 = 0.35       # primary torsional natural frequency (Hz)
F_NATURAL_2 = 0.82       # secondary torsional mode (Hz)
K_DRAIN = 2.0            # theta_twist drain rate (1/s)

# Stribeck friction parameters
T_STATIC = 5500.0        # static friction torque (Nm) — peak at zero velocity
T_COULOMB = 3500.0       # Coulomb friction (Nm) — constant sliding term
V_STRIBECK = 0.3         # Stribeck velocity (rad/s) — transition width
STR_SLOPE = 0.15         # high-speed friction rise slope (Nm·s/rad)

# Noise floors
NOISE_RPM_STABLE = 0.8
NOISE_RPM_SEVERE = 4.0
NOISE_TQ_STABLE = 15.0
NOISE_TQ_SEVERE = 60.0


def _smooth_envelope(t: np.ndarray) -> np.ndarray:
    """Smooth severity envelope using logistic transitions."""
    sev = np.zeros_like(t)
    mask = t < 20.0
    sev[mask] = 0.01
    mask = (t >= 20.0) & (t < 40.0)
    x = (t[mask] - 20.0) / 20.0
    sev[mask] = 0.01 + 0.84 / (1.0 + np.exp(-10.0 * (x - 0.5)))
    mask = (t >= 40.0) & (t < 65.0)
    sev[mask] = 0.85 + 0.08 * np.sin(2 * np.pi * 0.08 * t[mask])
    mask = (t >= 65.0) & (t < 80.0)
    x = (t[mask] - 65.0) / 15.0
    sev[mask] = 0.10 + 0.75 / (1.0 + np.exp(8.0 * (x - 0.5)))
    mask = (t >= 80.0) & (t < 85.0)
    sev[mask] = 0.10 * np.exp(-0.8 * (t[mask] - 80.0))
    mask = t >= 85.0
    sev[mask] = 0.005
    return sev


def _stribeck_friction(omega_rel: float, severity: float = 1.0) -> float:
    """FRICTION_BASE baseline + severity-scaled velocity-weakening peak."""
    abs_omega = abs(omega_rel)
    base = FRICTION_BASE
    if abs_omega < 1e-6:
        peak = T_STATIC * severity + FRICTION_BASE * (1.0 - severity)
        return peak
    stribeck_sign = np.sign(omega_rel)
    drop = (T_STATIC - FRICTION_BASE) * severity
    stribeck = drop * np.exp(-abs_omega / V_STRIBECK)
    viscous = STR_SLOPE * omega_rel
    return base + stribeck * stribeck_sign + viscous


def generate(
    duration: float = 100.0, dt: float = 0.02
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = int(round(duration / dt))
    t = np.arange(n, dtype=np.float64) * dt
    sev = _smooth_envelope(t)

    rpm = np.empty(n, dtype=np.float64)
    torque = np.empty(n, dtype=np.float64)
    theta_twist = 0.0

    STICK_FRAC = 0.65

    for i in range(n):
        ti = t[i]
        s = sev[i]

        # ---- Asymmetric RPM waveform with two torsional modes ----
        phase1 = (ti * F_NATURAL_1 * (1.0 + 0.05 * np.sin(0.1 * ti))) % 1.0
        phase2 = (ti * F_NATURAL_2 * (1.0 + 0.03 * np.sin(0.15 * ti))) % 1.0

        if phase1 < STICK_FRAC:
            mod1 = -phase1 / STICK_FRAC
        else:
            mod1 = (phase1 - STICK_FRAC) / (1.0 - STICK_FRAC)

        if phase2 < STICK_FRAC:
            mod2 = -phase2 / STICK_FRAC
        else:
            mod2 = (phase2 - STICK_FRAC) / (1.0 - STICK_FRAC)

        max_drop = SURFACE_RPM * (0.85 + 0.10 * s)
        max_rise = SURFACE_RPM * (0.50 + 1.20 * s)
        second_mode_amp = 0.25 * s
        bit_rpm = (
            SURFACE_RPM
            + s * np.where(mod1 < 0, mod1 * max_drop, mod1 * max_rise)
            + second_mode_amp * SURFACE_RPM * mod2
        )
        bit_rpm = max(bit_rpm, 0.0)

        # ---- Coupled torque with Stribeck friction ----
        omega_bit = bit_rpm * 2 * np.pi / 60.0
        delta_omega = SURFACE_OMEGA - omega_bit

        theta_twist += (delta_omega - K_DRAIN * theta_twist) * dt
        theta_twist = np.clip(theta_twist, -0.3, 2.5)

        torsional = K_TORSION * theta_twist + C_DAMPING * delta_omega
        friction = _stribeck_friction(delta_omega, severity=s)
        T_surface = friction + torsional

        # ---- Noise scales with severity ----
        rpm_noise_std = NOISE_RPM_STABLE + (NOISE_RPM_SEVERE - NOISE_RPM_STABLE) * s
        tq_noise_std = NOISE_TQ_STABLE + (NOISE_TQ_SEVERE - NOISE_TQ_STABLE) * s
        rpm[i] = bit_rpm + RNG.normal(0, rpm_noise_std)
        torque[i] = max(T_surface + RNG.normal(0, tq_noise_std), 100.0)

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
        fmt=["%.2f", "%.2f", "%.2f"],
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
        (0, 20, "normal"),
        (20, 40, "onset"),
        (40, 65, "severe"),
        (65, 80, "mitigation"),
        (80, 100, "recovery"),
    ]
    for start, end, label in phases:
        mask = (t >= start) & (t < end)
        mean_rpm = rpm[mask].mean()
        std_rpm = rpm[mask].std()
        mean_tq = torque[mask].mean()
        std_tq = torque[mask].std()
        print(
            f"  {label:>12s} ({start:3.0f}-{end:3.0f}s): "
            f"RPM {mean_rpm:5.0f}±{std_rpm:4.0f}  "
            f"Tq {mean_tq:5.0f}±{std_tq:4.0f}"
        )


if __name__ == "__main__":
    print("Generating realistic stick-slip data…")
    t, rpm, torque = generate(duration=100.0, dt=0.02)
    save(t, rpm, torque)
    print_stats(t, rpm, torque)
    print(f"Written: {OUTPUT}")
