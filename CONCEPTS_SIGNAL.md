# Signal Processing & Domain Concepts

## What is a signal?

A signal is a sequence of measurements taken over time. In drilling, two key signals are:

- **RPM** - rotational speed of the drill string (revolutions per minute)
- **Torque** - torsional load on the drill string

Each measurement is a **sample**. The **sample rate** (50 Hz in this pipeline) determines how many samples are taken per second. At 50 Hz, one sample arrives every 20 ms.

A 5‑second window at 50 Hz contains 250 samples - enough to capture several cycles of the dominant vibration.

## Time domain vs frequency domain

**Time domain**: the signal as it arrives - amplitude (RPM value) plotted against time. A sinusoidal RPM variation looks like a wave.

**Frequency domain**: the same signal decomposed into its constituent frequencies - amplitude plotted against frequency. A pure 1 Hz sine wave becomes a single spike at 1 Hz.

The **Fourier Transform** converts between these two representations. The system uses the **Fast Fourier Transform (FFT)**, an efficient O(n log n) algorithm for discrete sampled data.

## The Nyquist frequency

A fundamental constraint: to measure a frequency f, you must sample at **at least 2f** (the Nyquist rate). Below that, **aliasing** occurs — high frequencies masquerade as low ones.

With a sample rate of 50 Hz, the Nyquist frequency is 25 Hz. The bandpass filter (0.5–8.0 Hz) stays well within this limit, leaving a comfortable margin.

## Detrending

Raw sensor data often has:

- **DC offset** - a constant bias (e.g., RPM reading 120 when the true baseline is 0)
- **Linear drift** - slow changes due to temperature, sensor warming, or mechanical settling

The `scipy.signal.detrend` function fits a straight line to the data and subtracts it. This prevents low-frequency bias from dominating the FFT.

Before detrending: the FFT shows a large spike at 0 Hz (DC).
After detrending: the FFT shows only the oscillatory content.

## Bandpass filtering

A bandpass filter passes frequencies within a range and attenuates everything outside.

- **Low cut**: 0.5 Hz - removes very slow trends and DC (though detrend handles most of this)
- **High cut**: 8.0 Hz - removes high-frequency noise from the surface environment
- **Order**: 4 - steepness of the roll-off

The filter is implemented as a **Butterworth** filter in **second-order section (SOS)** form, which is numerically more stable than direct polynomial forms, especially for higher orders.

`scipy.signal.sosfiltfilt` applies the filter **forward and backward**, resulting in zero phase distortion — the output peaks line up with the input peaks.

## Windowing and spectral leakage

The FFT assumes the signal in the window repeats perfectly — that the left edge connects seamlessly to the right edge. Real signals rarely do this. The discontinuity at the edges creates **spectral leakage**: energy from the true frequency spills into neighboring bins.

A **Hann window** tapers the edges of the signal to zero, eliminating the discontinuities:

```bash
Before window:     ▁▂▃▄▅▆▇█▇▆▅▄▃▂▁   (sharp edges)
After Hann window: ▁▁▂▃▄▅▆▇▆▅▄▃▂▁▁   (smooth at edges)
```

The trade-off: the main peak broadens slightly, but the noise floor drops significantly.

## The FFT in detail

```bash
n = 250 samples
freqs = rfftfreq(250, d=1.0/50.0)   → 126 bins from 0 to 25 Hz
magnitudes = |rfft(samples)| / 250   → normalized magnitude per bin
peak_idx = argmax(magnitudes)         → dominant frequency
severity = sqrt(mean(magnitudes^2))   → RMS energy of the spectrum
```

`rfft` is used instead of `fft` because the input is real-valued — the negative frequencies are redundant, so `rfft` returns only the positive half.

## Stick-slip torsional vibration

In rotary drilling, the top drive rotates the drill string at a constant surface RPM. But friction between the drill string and the borehole wall causes the bit to **stick** (slow down, sometimes stop) and then **slip** (accelerate suddenly).

This creates a torsional pendulum: the drill string twists like a spring, storing elastic energy during the stick phase and releasing it during the slip phase. The result is a near-sinusoidal oscillation in bit RPM superimposed on the nominal rotation.

The natural frequency of this torsional oscillation depends on the drill string's material properties and geometry:

```bash
  fm = (1 / 2L) · √(G / ρ)
```

Where:

- L = drill string length (1000 m in the default config)
- G = shear modulus (80 GPa for steel)
- ρ = material density (7850 kg/m³ for steel)

For the default parameters: fm ≈ 0.5 Hz — one stick-slip cycle every 2 seconds.

## Sideband detection

When a carrier signal (the nominal RPM) is modulated by a lower-frequency vibration, it creates **sidebands** in the frequency spectrum — additional peaks at ±fm, ±2fm, ±3fm, ... from the carrier:

```bash
Magnitude
  │         fc
  │         ██
  │    ██  ██  ██
  │   ████ ██ ████
  │  ██████████████
  │ █████████████████
  └────────────────────▶ Frequency
     fc-fm  fc  fc+fm
```

The algorithm:

1. Find the carrier frequency `fc` (the highest spectral peak)
2. For each order n = 1, 2, 3, search for peaks at `fc ± n·fm`
3. A sideband is "detected" if its magnitude exceeds 5 % of the carrier magnitude
4. Each sideband gets a search window of ±0.15 Hz to account for FFT bin resolution

## Modulation index

The modulation index (MI) is the highest sideband-to-carrier ratio:

```bash
MI = max(sb_ratios)   where   sb_ratio = peak_magnitude / carrier_magnitude
```

- MI ≈ 0 → no modulation, no stick-slip
- MI > 0.3 → strong modulation, stick-slip likely developing
- MI > 0.5 → severe modulation, mitigation probably needed

The exact thresholds are tuned per well. The default `MITIGATE` threshold is dMI/dt ≥ 0.005/s — the **rate of change** of MI, not its absolute value.

## Growth rate (dMI/dt)

A snapshot of MI doesn't tell the full story. What matters is the **trend**: is the modulation getting worse?

`ModulationHistory` stores the last 30 MI values with their timestamps. At each assessment, it fits a straight line:

```bash
MI(t) = growth_rate · t + intercept
```

via `numpy.linalg.lstsq` — a linear least-squares solution that minimises the sum of squared residuals.

The slope `growth_rate` is dMI/dt:

- Positive → modulation is intensifying
- Negative → modulation is subsiding
- Near zero → modulation is stable

## Assessment flowchart

```bash
Sidebands present around fc?
  No  ──────────────────────────────────────────────▶ MINIMAL
  Yes ──▶ Are sidebands growing (dMI/dt > 0.001/s)?
             No  ──────────────────────────────────▶ STABLE
             Yes ──▶ Is dMI/dt ≥ 0.005/s?
                       No  ────────────────────────▶ INTENSIFYING
                       Yes ────────────────────────▶ MITIGATE
```

| Status | Meaning |
|---|---|
| `MINIMAL` | No sidebands detected — no torsional vibration |
| `STABLE` | Sidebands present but not growing — ongoing but not worsening |
| `INTENSIFYING` | Sidebands growing but below the mitigate threshold — watch closely |
| `MITIGATE` | Sidebands growing fast — action required |

## Severity index

The `SpectralResult` includes a `severity_index = sqrt(mean(magnitudes²))` — the RMS energy of the full spectrum. This is a simple heuristic, not a physics-based metric. It provides a compact single-number summary but is not used by the assessment decision tree (which relies on sidebands and growth rate instead).

## The event model

`StickSlipEvent` is the final output — a versioned, transport-agnostic record that carries everything a downstream system needs:

```python
@dataclass(frozen=True)
class StickSlipEvent:
    version: str           # Schema version for forward compatibility
    source: str            # Identifier of the pipeline instance
    timestamp: float       # When this assessment was made
    channel: str           # Which sensor channel
    status: str            # MINIMAL / STABLE / INTENSIFYING / MITIGATE
    carrier_frequency: float
    modulation_frequency: float
    modulation_index: float
    growth_rate: float     # dMI/dt
    sidebands_present: bool
    sidebands_growing: bool
```
