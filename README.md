# stickslip

Functional-core / imperative-shell pipeline for detecting stick-slip torsional vibration from drilling RPM data.

## Quick start

```bash
uv sync
uv run stickslip
```

Reads `test.csv` (oscillating `bit_rpm` at 0.1 s intervals), runs the detection pipeline, and prints one assessment per analysis window.

```text
[   RPM] status=MINIMAL mi=0.0000 g=+0.00000/s
[   RPM] status=STABLE  mi=0.4673 g=+0.00010/s
[   RPM] status=STABLE  mi=0.4668 g=-0.00020/s
```

## Architecture

```bash
┌─ Shell (I/O) ──────────────────────────────────────┐
│  csv_source → csv_chunk_stream → RollingBuffer     │
│                                   ↓                │
├─ Pure Core (no I/O) ───────────────────────────────┤
│  Signal → detrend → bandpass → window → fft_analyze│
│                              ↓                     │
│              detect_sidebands + ModulationHistory  │
│                              ↓                     │
│                   assess → StickSlipEvent → sink   │
└────────────────────────────────────────────────────┘
```

The shell owns acquisition and timing. The core is pure — every function is deterministic and side-effect free. A caller-supplied `sink` callback receives events at the boundary, keeping detection decoupled from display or mitigation.

### Pipeline stages

| Stage | Module | What it does |
|---|---|---|
| `csv_chunk_stream` | `shell.py` | Reads `bit_rpm` from CSV, yields fixed-size chunks at a target sample rate |
| `RollingBuffer.push` | `buffer.py` | Appends chunks to an immutable window, drops old samples |
| `detrend` | `transforms.py` | Removes linear drift and DC offset |
| `bandpass(0.5, 8.0)` | `transforms.py` | Isolates the torsional vibration band |
| `windowed("hann")` | `transforms.py` | Reduces FFT spectral leakage |
| `fft_analyze` | `transforms.py` | Computes magnitude spectrum, peak frequency, severity index |
| `detect_sidebands` | `sidebands.py` | Searches for upper/lower sideband peaks around the carrier |
| `ModulationHistory` | `history.py` | Tracks modulation index over time via a fixed-size ring buffer; fits a linear trend with `lstsq` |
| `assess` | `assessment.py` | Flowchart decision tree: sidebands present? → growing? → above mitigate threshold? |
| `StickSlipEvent` | `types.py` | Versioned, transport-agnostic output event |

### Detection flowchart

```bash
Sidebands present around fc?
  No  → MINIMAL
  Yes → Are sideband peaks growing over time (dMI/dt > 0)?
           No  → STABLE
           Yes → Is dMI/dt ≥ MITIGATE threshold?
                    No  → INTENSIFYING
                    Yes → MITIGATE
```

All state is isolated in `ModulationHistory` (a ring buffer of contiguous `float64` arrays). Every other object is frozen — `push()` returns a new buffer, transforms return new `Signal` instances.

## Event boundary

`cli.run()` accepts `sink: Callable[[StickSlipEvent], None]`. Every analysis window produces one event and passes it to the sink. The default sink prints to console; a downstream system can replace it to publish to Kafka, MQTT, or a dashboard.

```python
def my_sink(event: StickSlipEvent) -> None:
    if event.status == "MITIGATE":
        alerts.send(f"Stick-slip detected on {event.channel}")

run(..., sink=my_sink)
```

## Project structure

```bash
stickslip/
  __init__.py    — Public exports (domain types + pure functions only)
  types.py       — Frozen dataclasses: Signal, SpectralResult, SidebandResult,
                   StickSlipAssessment, StickSlipEvent, etc.
  buffer.py      — RollingBuffer (immutable, np.ndarray-backed)
  transforms.py  — Pure signal transforms: detrend, bandpass, lowpass, fft_analyze
  sidebands.py   — Sideband detection (data-oriented: parallel np.ndarray columns)
  history.py     — ModulationHistory ring buffer + lstsq growth rate
  assessment.py  — Decision tree: MINIMAL → STABLE → INTENSIFYING → MITIGATE
  shell.py       — CSV acquisition, simulated sensor stream
  display.py     — Console display adapter (throttled_display, render_display)
  cli.py         — CLI entry point, pipeline orchestration, event sink
  pipeline.py    — compose() utility
tests/
  test_core.py       — Buffer, sensor stream, CSV source, FFT pipeline
  test_sidebands.py  — Sideband detection, history, assessment, event shape
```

## Key design decisions

- **Single-channel pipeline** — RPM only. The event boundary makes it trivial to add channels: each channel runs its own pipeline and emits events with a `channel` tag.
- **No mitigation logic in the detector** — `assess()` only classifies. Mitigation is a downstream concern.
- **Data-oriented sidebands** — `SidebandResult` stores peaks as parallel `np.ndarray` columns (`sb_orders`, `sb_is_upper`, `sb_ratios`, etc.) instead of a tuple of dataclass objects.
- **`compose()` only** — `pipe`, `tap`, and `fanout` were removed; a single left-to-right composition function is sufficient.
