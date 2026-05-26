# Python & Programming Concepts

## Functional core / imperative shell

This project separates every function into one of two layers:

- **Pure core** — functions that take data in and return data out with no side effects. No I/O, no prints, no network calls. Given the same input, they always return the same output. Every function in `transforms.py`, `sidebands.py`, `assessment.py`, and `pipeline.py` is pure.
- **Imperative shell** — code that talks to the outside world. Reading from a CSV, sleeping to match a sample rate, printing output. All of it lives in `shell.py` and `cli.py`.

The boundary is strict: the core never imports from the shell. The shell imports from the core. This makes the core independently testable — you don't need a CSV file or real sensor to test the detection logic.

```bash
shell.py ──imports──▶ transforms.py   (pure)
                     sidebands.py     (pure)
                     assessment.py    (pure)
                     buffer.py        (immutable)
                     history.py       (isolated mutable state)
```

## Immutability

Once created, an object's data never changes. To "update" it, you create a new instance with the changes applied.

**Frozen dataclasses** (`@dataclass(frozen=True)`) enforce this at the language level — trying to set an attribute raises an error. Every type in `types.py` (`Signal`, `SpectralResult`, `SidebandResult`, `StickSlipAssessment`, `StickSlipEvent`) is frozen.

**RollingBuffer** doesn't mutate — `buffer.push(new_samples)` returns a **new** `RollingBuffer` with the combined data. The old buffer is untouched:

```python
buf1 = make_buffer(1.0, 4.0, "RPM")     # data = []
buf2 = buf1.push(np.array([1.0, 2.0]))   # data = [1.0, 2.0]
# buf1 still has data = []
```

This eliminates entire categories of bugs. No shared state, no unexpected mutation, no need to reason about what changed what.

## Data-oriented design

Instead of an "array of structs" (a list of peak objects, each with its own fields), `SidebandResult` uses a "struct of arrays" — six parallel `numpy.ndarray` columns:

```python
# Array of structs (old approach, removed):
sidebands: tuple[SidebandPeak, ...]
# Access: result.sidebands[0].ratio, result.sidebands[1].ratio

# Struct of arrays (current):
sb_orders: np.ndarray     # shape (N,) int32
sb_is_upper: np.ndarray   # shape (N,) bool
sb_expected_hz: np.ndarray
sb_actual_hz: np.ndarray
sb_magnitudes: np.ndarray
sb_ratios: np.ndarray
# Access: result.sb_ratios[0], result.sb_ratios[1]
```

Benefits:

- No per-access tuple-to-array conversion — the arrays are the storage
- Vectorized operations work directly: `np.mean(result.sb_ratios)`
- Better cache locality for numerical processing

## Function composition

`compose()` chains functions left-to-right so the pipeline reads in execution order:

```python
process = compose(detrend, bandpass(0.5, 8.0), windowed("hann"), fft_analyze)
result = process(signal)
# Equivalent to: fft_analyze(windowed("hann")(bandpass(0.5, 8.0)(detrend(signal))))
```

Implemented with `functools.reduce`:

```python
def compose(*fns):
    return functools.reduce(lambda f, g: lambda x: g(f(x)), fns)
```

Helpers like `bandpass()`, `lowpass()`, and `windowed()` return partially applied functions via `functools.partial`, so they slot directly into the composition chain.

This is the only pipeline utility — `pipe`, `tap`, and `fanout` were removed because a single composition function was enough.

## Ring buffer pattern

`ModulationHistory` is the only mutable object in the system. It uses a classic ring buffer:

```python
class ModulationHistory:
    def __init__(self, capacity: int = 30):
        self._times = np.zeros(capacity, dtype=np.float64)
        self._mi_values = np.zeros(capacity, dtype=np.float64)
        self._count = 0
        self._head = 0

    def update(self, result: SidebandResult) -> None:
        self._times[self._head] = result.timestamp
        self._mi_values[self._head] = result.modulation_index
        self._head = (self._head + 1) % self._capacity
        self._count += 1
```

- Pre-allocated fixed-size arrays — zero allocations at runtime after construction
- `_head` wraps around with modular arithmetic, overwriting the oldest entry
- `_ordered_slice()` reconstructs chronological order when the buffer has wrapped
- Growth rate computed via `numpy.linalg.lstsq` — a linear least-squares fit of MI vs time

This is isolated state — it's the only place where mutation happens, and it's contained in a small, auditable class.

## Event boundary (sink callback)

The pipeline doesn't know or care what happens after detection. It emits a `StickSlipEvent` to a caller-supplied callback:

```python
EventSink = Callable[[StickSlipEvent], None]

def run(*, sink: Optional[EventSink] = None) -> None:
    ...
    if sink is not None:
        sink(StickSlipEvent(...))
```

The sink can print to console, publish to Kafka, send to a WebSocket, or log to a file. The detector doesn't change — it just calls the callback.

This is the **Open-Closed Principle** in practice: the pipeline is closed for modification but open for extension through the sink.

## Type hints everywhere

Every function signature includes types:

```python
def detect_sidebands(
    spectral: SpectralResult,
    fm: float,
    n_max: int = 3,
    search_window_hz: float = 0.15,
    min_ratio: float = 0.05,
) -> SidebandResult: ...

def assess(
    sideband_result: SidebandResult,
    growth_rate: float,
    is_growing: bool,
    mitigate_threshold: float = 0.005,
) -> StickSlipAssessment: ...
```

Types serve as documentation the compiler can check. `from __future__ import annotations` makes all annotations strings (PEP 563), avoiding runtime evaluation.

## NumPy vectorization

Hot-path operations use NumPy instead of Python loops:

```python
# Good (vectorized):
freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
magnitudes = np.abs(np.fft.rfft(samples)) / n
peak_idx = int(np.argmax(magnitudes))

# Instead of:
# for i in range(n):  slow!
```

The `_search` helper in `sidebands.py` uses `np.searchsorted` and `np.argmax` for O(log n) bin search instead of scanning.

## Generator streaming

`csv_chunk_stream` is a generator — it yields chunks forever without loading the entire file into memory:

```python
def csv_chunk_stream(...) -> Generator[tuple[np.ndarray, float], None, None]:
    reader = csv_source(data)
    while True:
        t0 = time.time()
        chunk = np.array([reader() for _ in range(chunk_size)])
        yield chunk, t0
        # sleep to maintain sample rate
```

The CLI consumes it with `zip(range(max_ticks), ...)` which naturally stops after `max_ticks` iterations.

## Why this matters for testing

Because the core is pure and types are frozen, tests are straightforward:

- No mocking needed — pure functions are called with test data
- No setup/teardown for state — each call is independent
- No I/O in the core — tests don't need files or sensors
- Assertions are simple — given input X, expect output Y

```python
def test_detects_sidebands():
    result = detect_sidebands(_spectral(), fm=0.5, n_max=1)
    assert result.sidebands_present
```
