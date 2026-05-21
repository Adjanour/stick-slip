# Stick-Slip Pipeline Design

## Overview

This repository uses a functional core / imperative shell structure.

The pure core transforms `Signal` values into analysis results. The shell owns acquisition, timing, and display.

## Pipeline

1. Acquisition
   - Samples raw surface `RPM` and `Torque`
   - Default cadence is `50Hz` (`Δt = 0.02s`)

2. Windowing
   - Uses an immutable rolling buffer
   - Default window is `5s`
   - At `50Hz`, the window holds `250` samples per channel

3. Processing
   - `detrend()` removes linear drift and DC offset
   - `bandpass(0.5, 8.0)` strips low-frequency background and high-frequency noise
   - `windowed("hann")` reduces FFT leakage

4. Calculation
   - `fft_analyze()` computes the frequency spectrum
   - It returns peak frequency, peak magnitude, and a severity index

5. Output
   - `throttled_display(1.0)` emits display updates at most once per second per channel
   - `render_display()` prints the result for the driller's screen

## Current Notes

- The acquisition layer is simulated by default.
- The CLI supports both `RPM` and `Torque` channels.
- Output throttling is per-channel, not global.
- The pure core is testable in isolation.
