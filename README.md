# stickslip

Dual-track stick-slip detection and mitigation pipeline for drilling automation.
Functional-core / imperative-shell architecture — two parallel detection tracks
feed a rule-based mitigation controller that emits setpoint adjustments.

## Quick start

```bash
uv sync
uv run stickslip
```

Reads `test.csv` (oscillating `bit_rpm` and `torque` at 0.1 s intervals), runs both
detection tracks in parallel threads, and prints events plus mitigation signals.

```text
[   RPM] sb=STABLE mi=0.3488 g=+0.00000/s
[ENERGY] status=ENERGY_NORMAL U=0.00J peak=0.00J drop=0.00%
[   RPM] sb=MITIGATE mi=0.4487 g=+0.24971/s
[MITIGATE] rpm=110 wob=40000 — stick-slip mitigation (MI=0.449)
```

## Architecture

```text
                         ┌────────────────────────────────────────┐
                         │  ThreadPoolExecutor (max_workers=2)    │
                         │                                        │
RPM CSV ────────────────→│  _sideband_track                       │
  (csv_chunk_stream)     │    RollingBuffer → detrend             │
                         │    → bandpass → window → fft_analyze   │
                         │    → detect_sidebands → ModulationHist │
                         │    → assess → StickSlipEvent ──────────┤──┐
                         │                                        │  │
Torque CSV ─────────────→│  _energy_track                         │  │
  (csv_chunk_stream)     │    RollingBuffer → compute_energy      │  │
                         │    → EnergyHistory → assess            │  │
                         │    → EnergyEvent ──────────────────────┤──┤
                         └────────────────────────────────────────┘  │
                                                                     ├──→ MitigationController
                                                                     │       ↓
                                                                     │   MitigationSignal
                                                                     │  (rpm_setpoint,wob_setpoint,reason)
                                                                     │  
                                                                     │   
```

Every function before the event boundary is deterministic and side-effect free.
The `MitigationController` is the only stateful consumer — it tracks current
setpoints and ramps back to baseline when conditions normalise.

### Detection tracks

| Track | Input | Pipeline | Output |
|---|---|---|---|
| **Sideband** | RPM | RollingBuffer → detrend → bandpass(0.5–8.0 Hz) → Hann window → FFT → detect_sidebands → ModulationHistory → assess | `StickSlipEvent` |
| **Energy** | Torque | RollingBuffer → compute_energy (polar moment, temperature-derated G, series-spring stiffness) → EnergyHistory → assess | `EnergyEvent` |

### Sideband decision tree

```text
Sidebands present around fc?
  No  → MINIMAL
  Yes → Are sideband peaks growing over time (dMI/dt > 0)?
           No  → STABLE
           Yes → Is dMI/dt ≥ MITIGATE threshold?
                    No  → INTENSIFYING
                    Yes → MITIGATE
```

### Energy decision tree

```text
Buffer warm (≥ capacity)?
  No  → ENERGY_NORMAL
  Yes → Sharp drop from peak (≥ 50%)?
           Yes → ENERGY_RELEASE
           No  → Recent values trending up?
                    Yes → ENERGY_BUILDING
                    No  → ENERGY_NORMAL
```

### Mitigation controller rules

| Trigger | RPM adjustment | WOB adjustment |
|---|---|---|
| INTENSIFYING | +15% | −30% |
| MITIGATE | +10% | −20% |
| STABLE / MINIMAL | Ramp 5%/step toward baseline | Ramp 5%/step toward baseline |
| ENERGY_RELEASE | +20% | −50% |
| ENERGY_BUILDING | No change | −15% |

### Pipeline stages

| Stage | Module | What it does |
|---|---|---|
| `csv_chunk_stream` | `shell.py` | Reads `bit_rpm` or `torque` from CSV, yields fixed-size chunks |
| `RollingBuffer.push` | `buffer.py` | Appends chunks to an immutable window, drops old samples |
| `detrend` | `transforms.py` | Removes linear drift and DC offset |
| `bandpass(0.5, 8.0)` | `transforms.py` | Isolates the torsional vibration band |
| `windowed("hann")` | `transforms.py` | Reduces FFT spectral leakage |
| `fft_analyze` | `transforms.py` | Computes magnitude spectrum, peak frequency, severity index |
| `detect_sidebands` | `sidebands.py` | Searches for upper/lower sideband peaks around the carrier |
| `ModulationHistory` | `history.py` | Tracks modulation index over time; fits linear trend with `lstsq` |
| `assess` | `assessment.py` | Decision tree: MINIMAL → STABLE → INTENSIFYING → MITIGATE |
| `compute_energy` | `energy.py` | `J=π/32·(OD⁴−ID⁴)`, temperature-derated G, series-spring K, `U=T²/2K` |
| `EnergyHistory` | `energy.py` | Ring buffer of energy values, detects sharp drops |
| `MitigationController` | `mitigation.py` | Consumes `StickSlipEvent` + `EnergyEvent`, emits `MitigationSignal` |

### Types

| Type | Module | Fields |
|---|---|---|
| `StickSlipEvent` | `types.py` | `version`, `source`, `timestamp`, `channel`, `status`, `carrier_frequency`, `modulation_frequency`, `modulation_index`, `growth_rate`, `sidebands_present`, `sidebands_growing` |
| `EnergyEvent` | `types.py` | `version`, `source`, `timestamp`, `status`, `energy`, `peak_energy`, `drop_ratio`, `t_bit`, `k_total`, `bit_depth` |
| `MitigationSignal` | `types.py` | `version`, `source`, `timestamp`, `rpm_setpoint`, `wob_setpoint`, `reason` |

## Event boundary

Both detection tracks emit versioned, transport-agnostic events to caller-supplied
sink callbacks. The `MitigationController` subscribes to both event types and
outputs `MitigationSignal` to its own sink — keeping detection, mitigation logic,
and actuation decoupled.

```python
def my_sink(signal: MitigationSignal) -> None:
    # Publish RPM/WOB setpoints to OpenLab via D-WIS
    dwis.send_setpoints(rpm=signal.rpm_setpoint, wob=signal.wob_setpoint)

controller = MitigationController(
    baseline_rpm=100.0, baseline_wob=50000.0,
    sink=my_sink,
)
run(
    stick_slip_sink=controller.on_stick_slip,
    energy_sink=controller.on_energy,
)
```

## Project structure

```bash
stickslip/
  __init__.py     — Public exports
  types.py        — Frozen dataclasses: Signal, SpectralResult, SidebandResult,
                    TorsionalEnergyResult, EnergyAssessment, StickSlipAssessment,
                    StickSlipEvent, EnergyEvent, MitigationSignal, etc.
  buffer.py       — RollingBuffer (immutable, np.ndarray-backed)
  transforms.py   — Pure signal transforms: detrend, bandpass, lowpass, fft_analyze
  sidebands.py    — Sideband detection (data-oriented: parallel np.ndarray columns)
  history.py      — ModulationHistory ring buffer + lstsq growth rate
  assessment.py   — Decision tree: MINIMAL → STABLE → INTENSIFYING → MITIGATE
  energy.py       — Torsional energy: polar moment, temperature-derated G,
                    series-spring stiffness, EnergyHistory ring buffer
  mitigation.py   — MitigationController: consumes events, emits setpoint signals
  shell.py        — CSV acquisition (column-parameterised), simulated sensor stream
  display.py      — Console display adapter
  cli.py          — CLI entry point, threaded orchestration, event sinks
  pipeline.py     — compose() utility
tests/
  test_core.py       — Buffer, sensor stream, CSV source, FFT pipeline
  test_sidebands.py  — Sideband detection, history, assessment, event shape
  test_energy.py     — Energy pure functions, EnergyHistory, event shape
  test_mitigation.py — MitigationController rules, ramping, signal shape
```

## Key design decisions

- **Two parallel detection tracks** — RPM sidebands and torque energy run in separate threads, each with independent buffers and state. The `ThreadPoolExecutor` (max_workers=2) keeps them isolated.
- **Transport-agnostic event boundary** — Detection never knows about mitigation or actuation. `StickSlipEvent`, `EnergyEvent`, and `MitigationSignal` are versioned dataclasses any service can consume.
- **Mitigation is a separate consumer** — `MitigationController` subscribes to both event types and emits setpoint signals. Swap it out for a PID controller or ML policy without touching detection.
- **Temperature-derated torsional model** — Shear modulus G drops 2.3% per 100 °C above surface temperature. Segments (drill collar, HWDP, drill pipe) act as springs in series.
- **Data-oriented sidebands** — `SidebandResult` stores peaks as parallel `np.ndarray` columns instead of tuples.
- **No pandas** — CSV reading uses `np.genfromtxt`.
