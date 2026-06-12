# stickslip

Dual-track stick-slip detection and mitigation pipeline for drilling automation.
Reads RPM and torque (from CSV or live OPC-UA), runs two parallel detection
threads, and outputs mitigation setpoints (RPM/WOB adjustments).

## Quick start

```bash
uv sync
uv run stickslip
```

Default mode prints events and mitigation signals to the terminal as they stream
through the 100-second test CSV:

```text
[ENERGY] status=ENERGY_NORMAL U=0.00J peak=0.00J drop=0.00%
[   RPM] sb=MITIGATE mi=0.4991 g=+0.00000/s
[MITIGATE] rpm=110 wob=40000 — stick-slip mitigation (MI, priority=50)
```

## UI modes

| Flag | Mode | Extra deps |
|------|------|------------|
| *(none)* | Rich terminal dashboard | — |
| `--dashboard` | Rich live-updating dashboard | — |
| `--tui` | Interactive Textual TUI (keyboard) | `uv sync --group tui` |
| `--web` | FastHTML web UI (browser) | `uv sync --group web` |

```bash
uv run stickslip --web                    # web UI on http://0.0.0.0:8080
uv run stickslip --tui                    # interactive terminal UI
uv run stickslip --web --dashboard        # both
uv run stickslip --config config.toml     # TOML config override
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--duration-seconds` | 15.0 | How long to process (overrides CSV total) |
| `--window-seconds` | 5.0 | FFT window size |
| `--sample-rate` | 50.0 | Samples per second |
| `--chunk-size` | 10 | Samples per chunk |
| `--bit-depth` | 1000.0 | Current bit depth (m) |
| `--baseline-rpm` | 100.0 | Normal operating RPM |
| `--baseline-wob` | 50000.0 | Normal WOB (N) |
| `--dwis-endpoint` | — | OPC-UA endpoint for live data |
| `--host` / `--port` | 0.0.0.0:8080 | Web UI address |
| `--config` | — | Path to TOML config file |

All flags can also be set in `config.toml`.

## How it works

Two threads run in parallel, synced by a `threading.Barrier` so both read the
same CSV chunk at the same wall-clock time:

**Sideband track** (RPM) — detrend → bandpass (1–5 Hz) → FFT → detect FM
sidebands around the carrier frequency → track modulation index growth →
assess: MINIMAL → STABLE → INTENSIFYING → MITIGATE

**Energy track** (torque) — compute torsional energy from polar moment,
temperature-derated shear modulus, series-spring stiffness → track energy
build-up and sharp drops → assess: ENERGY_NORMAL → ENERGY_BUILDING →
ENERGY_RELEASE

The **MitigationController** fuses both assessments by priority and emits
RPM/WOB setpoint adjustments:

| Trigger | RPM | WOB |
|---------|-----|-----|
| INTENSIFYING | +15% | −30% |
| MITIGATE | +10% | −20% |
| ENERGY_RELEASE | +20% | −50% |
| ENERGY_BUILDING | — | −15% |
| STABLE / MINIMAL | Ramp to baseline | Ramp to baseline |

## Project structure

```
stickslip/
  types.py        — Frozen dataclasses (Signal, Events, config types)
  transforms.py   — Pure signal transforms (detrend, bandpass, FFT)
  sidebands.py    — FM sideband detection on RPM spectrum
  history.py      — Modulation index history + linear growth rate
  assessment.py   — Decision tree (MINIMAL → STABLE → INTENSIFYING → MITIGATE)
  energy.py       — Torsional energy model + EnergyHistory
  mitigation.py   — MitigationController (rule-based setpoint fusion)
  buffer.py       — RingBuffer (O(1) numpy ring buffer)
  shell.py        — CSV data source (SharedCsvSource)
  dwis.py         — OPC-UA connector stub for live competition data
  campbell.py     — Campbell diagram collector (RPM vs frequency)
  ssi.py          — Stick-Slip Severity Index (MI × 100)
  report.py       — HTML report generator
  dashboard.py    — Rich terminal dashboard
  tui.py          — Textual interactive TUI
  webui.py        — FastHTML web UI
  config.py       — Nested dataclass config + TOML loading
  cli.py          — CLI entry point + threaded orchestration
  pipeline.py     — compose() utility
test.csv          — 5000 rows, 50 Hz, 5 phases (normal → severe → recovery)
config.toml       — Default configuration
```

## Test data

`test.csv` contains 100 seconds (5000 rows at 50 Hz) of simulated drilling data
with Stribeck friction and two torsional modes:

| Phase | Time | What happens |
|-------|------|-------------|
| Normal | 0–20s | Stable RPM, low torque variation |
| Developing | 20–40s | Sidebands appear, MI growing |
| Severe | 40–65s | Strong stick-slip, high MI |
| Decaying | 65–85s | MI decreasing |
| Recovery | 85–100s | Back to normal |

## Dependencies

Runtime: `numpy`, `rich`, `scipy`

Optional:
- `textual` — TUI mode (`uv sync --group tui`)
- `python-fasthtml`, `uvicorn` — web UI mode (`uv sync --group web`)
- `matplotlib` — Campbell diagram rendering (`uv sync --group campbell`)

## Configuration

All parameters live in `stickslip/config.py` as nested dataclasses with
sensible defaults. Override via TOML:

```bash
uv run stickslip --config my_config.toml
```

See `config.toml` for all available fields.
